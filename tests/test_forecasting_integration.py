"""Offline end-to-end integration test (design §14 Integration).

Exercises the four-stage machinery on self-generated Tier C data: axis ->
features -> baseline residual -> QR fit -> calibration (both) -> band -> scenarios
-> metrics -> artifact write/read, asserting shapes, non-crossing post-rounding,
attestation completeness, and run-to-run digest equality. Network-touching paths
are not used here.
"""

from __future__ import annotations

import datetime as dt

import numpy as np

from monsoon_grid.forecasting.artifacts import load_artifact, save_artifact
from monsoon_grid.forecasting.axis import build_regular_axis
from monsoon_grid.forecasting.calibration import apply_cqr, cqr_score, empirical_coverage
from monsoon_grid.forecasting.features import build_features
from monsoon_grid.forecasting.interface import CalibratedBand, CoverageAttestation, round_rearrange_validate
from monsoon_grid.forecasting.metrics import crps_from_quantile_grid, pinball
from monsoon_grid.forecasting.quantile import clip_band, fit_linear_quantile_model, rearrange
from monsoon_grid.forecasting.scenarios import fast_forward_reduction

STEP = 900


def _synth(seed=0):
    rng = np.random.default_rng(seed)
    n = 1000
    ts = np.arange(n) * STEP
    tod = (ts % 86400) / 86400.0
    y = np.clip(np.sin(np.pi * np.clip((tod - 0.25) / 0.5, 0, 1)) * (1 - 0.3 * rng.random(n)), 0, 1) * 100.0
    fcst = np.clip(y + rng.normal(0, 5, n), 0, None)
    return ts, y, fcst


def test_end_to_end_chain_offline(tmp_path):
    ts, y, fcst = _synth()
    cap = 100.0
    ax = build_regular_axis(ts, y / cap, STEP)
    assert ax.axis_coverage == 1.0

    # Features for a window of valid times.
    issue = int(ts[600])
    valid = ts[601:611].astype(np.float64)
    fm = build_features(issue, valid, ax, nwp_forecast={"fcst": fcst[601:611] / cap})
    assert fm.matrix.shape[0] == 10

    # Residual QR on the training block.
    residual = (y - fcst) / cap
    X = np.column_stack([fcst / cap, np.sin(2 * np.pi * (np.arange(len(y)) % 96) / 96)])
    tr = slice(0, 600)
    taus = (0.1, 0.5, 0.9)
    model = fit_linear_quantile_model(X[tr], residual[tr], taus=taus)

    # Predict -> rearrange -> clip, on a test block.
    te = slice(700, 1000)
    rq = model.predict(X[te])  # already rearranged
    bands = fcst[te] / cap + rq  # per-unit band around the forecast
    bands = rearrange(bands)
    bands = clip_band(bands, 0.0, 1.05)

    # Calibrate the 80% band (tau 0.1/0.9) via CQR.
    lo = bands[0] * cap
    hi = bands[2] * cap
    scores = cqr_score(y[te], lo, hi)
    clo, chi = apply_cqr(lo, hi, scores, 0.2, min_width=0.5, clip_lo=0, clip_hi=105)
    cov = empirical_coverage(y[te], clo, chi)
    assert 0.0 <= cov <= 1.0

    # Metrics.
    p50 = (fcst[te] / cap + model.predict(X[te])[1]) * cap
    assert pinball(y[te], p50, 0.5).value >= 0.0
    quantiles = np.stack([clo, 0.5 * (clo + chi), chi]) / cap
    crps, omitted = crps_from_quantile_grid(y[te] / cap, np.array(taus), quantiles)
    assert omitted > 0

    # Scenarios reduction on a tiny raw set.
    rng = np.random.default_rng(1)
    raw = rng.normal(size=(100, 12))
    sel, probs = fast_forward_reduction(raw, 5)
    assert probs.sum() > 0.999

    # Artifact round-trip.
    arrays = {"coef": model.coefficients}
    manifest = {"regeneration_command": "run_quantile_training"}
    sha = save_artifact(tmp_path, "pv-residual-qr", "linear-v1", arrays, manifest)
    loaded = load_artifact(tmp_path, "pv-residual-qr", "linear-v1")
    assert loaded.manifest["content_sha256"] == sha

    # Interface band: rounded + non-crossing + complete attestation.
    rounded = round_rearrange_validate([[round(float(v), 6) for v in quantiles[i]][:1] for i in range(3)])
    att = CoverageAttestation(
        nominal_coverage=0.8,
        quantile_levels=list(taus),
        empirical_coverage_by_horizon={"h_4step_1h": round(cov, 6)},
        mean_width_by_horizon={"h_4step_1h": round(float(np.mean(chi - clo)), 6)},
        empirical_coverage_by_regime={},
        mean_width_by_regime={},
        sample_count_by_cell={"h_4step_1h": int(te.stop - te.start)},
        regime_presence_counts={},
        calibration_cell_fallback=[],
        measured_on_dataset_id="synthetic_tier_c",
        measured_on_dataset_sha256="0" * 64,
        baseline_source="provider_forecast",
        validation_tier="C",
        validation_status="tier_c_synthetic",
        caveats=["Tier C synthetic; no skill claim"],
    )
    assert att.validated is False  # Tier C is never "validated"
    band = CalibratedBand(
        band_id="b1",
        asset_id="pv1",
        variable="solar_availability",
        issue_time=dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc),
        valid_start=dt.datetime(2024, 6, 1, 1, tzinfo=dt.timezone.utc),
        valid_end=dt.datetime(2024, 6, 1, 1, 15, tzinfo=dt.timezone.utc),
        slot_minutes=15,
        quantile_levels=list(taus),
        quantiles=rounded,
        point_p50_mw=[round(float(rounded[1][0]), 6)],
        band_source=["model"],
        baseline_mw=[round(float(fcst[te][0]), 6)],
        baseline_source="provider_forecast",
        model_version="qr-linear-v1",
        calibration_version="cqr-v1",
        calibration_method="cqr",
        model_artifact_sha256=sha,
        calibration_artifact_sha256="b" * 64,
        coverage_attestation=att,
    )
    assert band.unit == "MW"


def test_run_to_run_digest_equality_tiny():
    import pathlib
    import sys

    # ``experiments`` lives at the repo root; make it importable regardless of cwd.
    repo_root = str(pathlib.Path(__file__).resolve().parents[1])
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from experiments._harness import ExperimentRun

    def digest():
        run = ExperimentRun(experiment="int", config={"k": 3}, seed=5)
        g = run.generators(["bootstrap", "scenarios"])
        run.record("a", g["bootstrap"].random(4).tolist())
        run.record("b", g["scenarios"].random(4).tolist())
        return run.build_artifact()["determinism_digest"]

    assert digest() == digest()
