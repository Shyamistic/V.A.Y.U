r"""Fit residual quantile models on synthetic Tier C data, select by validation
pinball, report the loser's loss (design §5.3 honesty rule).

Command:
    & $py .\monsoon_grid\experiments\run_quantile_training.py --config .\monsoon_grid\experiments\configs\quantile.json
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from _harness import ExperimentRun, load_config
from _synthetic import synthetic_as_issued_forecast, synthetic_pv_series

from monsoon_grid.forecasting.quantile import fit_gbm_quantile, fit_linear_quantile_model, pinball_loss

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg = load_config(args.config)
    run = ExperimentRun(
        experiment="quantile_training",
        config=cfg,
        seed=cfg["seed"],
        dataset_fingerprints={"synthetic_tier_c": "self_generated"},
        results_root=REPO / "experiments" / "results",
    )
    g = run.generators(["gbm_subsample", "data"])
    epoch, y = synthetic_pv_series(40, g["data"], capacity_mw=100.0)
    fcst = synthetic_as_issued_forecast(y, g["data"])
    residual = (y - fcst) / 100.0  # per-unit residual
    # Simple feature: the forecast per-unit level + a time-of-day proxy.
    X = np.column_stack([fcst / 100.0, np.sin(2 * np.pi * (np.arange(len(y)) % 96) / 96)])

    n = len(y)
    tr = slice(0, int(0.6 * n))
    va = slice(int(0.7 * n), int(0.8 * n))  # embargo gap approximated

    tau = 0.5
    n_rounds = min(cfg["gbm"]["n_rounds"], 60)
    min_leaf = cfg["gbm"]["min_samples_leaf"]
    lin = fit_linear_quantile_model(X[tr], residual[tr], taus=(tau,))
    gbm = fit_gbm_quantile(X[tr], residual[tr], tau, n_rounds=n_rounds, min_leaf=min_leaf)

    lin_val = pinball_loss(residual[va], lin.predict(X[va])[0], tau)
    gbm_val = pinball_loss(residual[va], gbm.predict(X[va]), tau)
    selected = "gbm" if gbm_val < lin_val else "linear"

    run.record(
        "model_selection",
        {
            "tau": tau,
            "linear_val_pinball": lin_val,
            "gbm_val_pinball": gbm_val,
            "selected": selected,
            "loser_pinball": max(lin_val, gbm_val),
            "note": "Tier C synthetic -- demonstrates the selection+honesty mechanism, not a skill claim.",
        },
    )
    out = run.write()
    print(f"wrote {out}; selected={selected} (lin={lin_val:.5f}, gbm={gbm_val:.5f})")


if __name__ == "__main__":
    main()
