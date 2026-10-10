r"""Compare EnbPI vs ACI vs uncalibrated coverage AND width on synthetic Tier C.

Coverage is never reported without width on the same row (design §6.6). Real
per-regime headline tables require >= 24 months of real data; on Tier C those are
withheld.

Command (run from experiments/):
    & $py .\run_calibration_compare.py --config .\configs\calibration.json
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from _harness import ExperimentRun, load_config

from monsoon_grid.forecasting.calibration import (
    aci_coverage_bound,
    apply_cqr,
    cqr_score,
    empirical_coverage,
    make_aci_state,
)

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg = load_config(args.config)
    run = ExperimentRun(
        experiment="calibration_compare",
        config=cfg,
        seed=cfg["seed"],
        dataset_fingerprints={"synthetic_tier_c": "self_generated"},
        results_root=REPO / "experiments" / "results",
    )
    g = run.generators(["data"])
    alpha = cfg["alpha_nominal_headline"]

    # Synthetic residuals with a slow drift (non-stationary).
    n = 4000
    drift = np.linspace(0, 1.0, n)
    y = g["data"].normal(0, 1, size=n) + drift
    lo = -1.0 + drift
    hi = 1.0 + drift

    calib = slice(0, 2000)
    test = slice(2000, n)
    uncal_cov = empirical_coverage(y[test], lo[test], hi[test])
    uncal_width = float(np.mean(hi[test] - lo[test]))

    scores = cqr_score(y[calib], lo[calib], hi[calib])
    clo, chi = apply_cqr(lo[test], hi[test], scores, alpha, min_width=0.01, clip_lo=-100, clip_hi=100)
    cqr_cov = empirical_coverage(y[test], clo, chi)
    cqr_width = float(np.mean(chi - clo))

    # ACI online over the test block.
    st = make_aci_state(alpha, gamma=cfg["aci"]["gamma"])
    aci_inside = 0
    for i in range(test.start, n):
        covered = clo[i - test.start] <= y[i] <= chi[i - test.start]
        aci_inside += int(covered)
        st.update(covered)
    aci_cov = aci_inside / (n - test.start)
    bound = aci_coverage_bound(alpha, cfg["aci"]["gamma"], n - test.start)

    run.record(
        "calibration_table",
        {
            "nominal_coverage": 1 - alpha,
            "uncalibrated": {"coverage": uncal_cov, "mean_width": uncal_width},
            "cqr_split": {"coverage": cqr_cov, "mean_width": cqr_width},
            "aci": {"coverage": aci_cov, "clip_fraction": st.clip_fraction},
            "aci_bound_vs_realized": {"bound": bound, "realized_miscoverage": st.realized_miscoverage},
            "note": "Tier C synthetic; coverage always paired with width. Real per-regime tables withheld (<24mo).",
        },
    )
    out = run.write()
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
