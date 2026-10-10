r"""End-to-end evaluation harness with a determinism check and honest
non-acquisition reporting (design §8.5, §16).

``--twice`` runs the whole pipeline twice in-process with the same seed and
asserts identical determinism_digest. A "Datasets not obtained -- conclusions
withheld" section lists every withheld metric. No synthetic substitution.

Command:
    & $py .\monsoon_grid\experiments\run_full_eval.py --config .\monsoon_grid\experiments\configs\full_eval.json --twice
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from _harness import ExperimentRun, load_config
from _synthetic import synthetic_as_issued_forecast, synthetic_pv_series

from monsoon_grid.forecasting.calibration import apply_cqr, cqr_score, empirical_coverage
from monsoon_grid.forecasting.errors import DeterminismError
from monsoon_grid.forecasting.metrics import crps_from_quantile_grid, sharpness

REPO = Path(__file__).resolve().parents[1]


def _one_pass(cfg: dict) -> dict:
    run = ExperimentRun(
        experiment="full_eval",
        config=cfg,
        seed=cfg["seed"],
        dataset_fingerprints={"synthetic_tier_c": "self_generated"},
        results_root=REPO / "experiments" / "results",
    )
    g = run.generators(["data"])
    epoch, y = synthetic_pv_series(30, g["data"], 100.0)
    fcst = synthetic_as_issued_forecast(y, g["data"])
    alpha = cfg["alpha_nominal"]
    lo = np.clip(fcst - 10, 0, None)
    hi = fcst + 10
    n = len(y)
    calib = slice(0, int(0.7 * n))
    test = slice(int(0.8 * n), n)
    scores = cqr_score(y[calib], lo[calib], hi[calib])
    clo, chi = apply_cqr(lo[test], hi[test], scores, alpha, min_width=0.5, clip_lo=0, clip_hi=105)
    cov = empirical_coverage(y[test], clo, chi)
    width = sharpness(clo, chi).value
    taus = np.array(cfg["taus"])
    quantiles = np.stack([np.quantile(fcst[test], t) * np.ones(test.stop - test.start) for t in taus])
    crps, omitted = crps_from_quantile_grid(y[test], taus, quantiles)

    run.record(
        "tier_c_machinery",
        {"coverage": cov, "mean_width": width, "crps_truncated": crps.value, "omitted_mass": omitted},
    )
    run.record(
        "datasets_not_obtained_conclusions_withheld",
        {
            "gefcom_solar": "Tier A pinball/CRPS/coverage withheld (no network)",
            "gefcom_wind": "Tier A withheld",
            "emsx": "Tier A 15-min withheld",
            "openmeteo": "Tier B pseudo-truth withheld",
            "nasa_power": "Tier B truth side withheld",
        },
    )
    run.record("allow_archive_as_issued", cfg.get("allow_archive_as_issued", False))
    artifact = run.build_artifact()
    run.write()
    return artifact


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--twice", action="store_true")
    args = ap.parse_args()
    cfg = load_config(args.config)
    a1 = _one_pass(cfg)
    print(f"pass 1 determinism_digest={a1['determinism_digest']}")
    if args.twice:
        a2 = _one_pass(cfg)
        print(f"pass 2 determinism_digest={a2['determinism_digest']}")
        if a1["determinism_digest"] != a2["determinism_digest"]:
            raise DeterminismError("determinism_digest differs across the two runs")
        print("DETERMINISM OK: identical digests")


if __name__ == "__main__":
    main()
