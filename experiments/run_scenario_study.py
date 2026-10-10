r"""Scenario study: independence-understates-tail demo + reduction stability.

The required numerical demonstration (independent per-asset sampling understates
joint tail risk vs dependence-preserving sampling) runs on synthetic Tier C
positively-correlated PV sites.

Command:
    & $py .\monsoon_grid\experiments\run_scenario_study.py --config .\monsoon_grid\experiments\configs\scenarios.json
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from _harness import ExperimentRun, load_config

from monsoon_grid.forecasting.scenarios import fast_forward_reduction, independence_understates_tail

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg = load_config(args.config)
    run = ExperimentRun(
        experiment="scenario_study",
        config=cfg,
        seed=cfg["seed"],
        dataset_fingerprints={"synthetic_tier_c": "self_generated"},
        results_root=REPO / "experiments" / "results",
    )
    g = run.generators(["scenarios"])

    n, d, cap = 20000, 5, 100.0
    rho = 0.9
    common = g["scenarios"].normal(0, 1, size=n)
    idio = g["scenarios"].normal(0, 1, size=(n, d))
    dep = np.clip(0.5 + 0.15 * (rho * common[:, None] + np.sqrt(1 - rho**2) * idio), 0, 1)
    dep_port = dep.sum(axis=1) * cap
    ind = dep.copy()
    for j in range(d):
        ind[:, j] = g["scenarios"].permutation(ind[:, j])
    ind_port = ind.sum(axis=1) * cap

    cmp = independence_understates_tail(dep_port, ind_port, tail_margin_mw=cfg["tail_margin_mw"])
    run.record(
        "independence_understates_tail",
        {
            "p05_dependent_mw": cmp.p05_dependent_mw,
            "p05_independent_mw": cmp.p05_independent_mw,
            "ratio": cmp.ratio,
            "es05_dependent_mw": cmp.es05_dependent_mw,
            "es05_independent_mw": cmp.es05_independent_mw,
            "understates": cmp.understates,
        },
    )

    # Reduction stability across K (synthetic raw paths).
    raw = g["scenarios"].normal(size=(cfg["n_raw"], d * 4))
    red = {}
    for k in [kk for kk in cfg["k_values"] if kk <= cfg["n_raw"]]:
        sel, probs = fast_forward_reduction(raw, k)
        red[str(k)] = {"n_selected": int(len(sel)), "prob_sum": float(probs.sum())}
    run.record("reduction_by_k", red)
    out = run.write()
    print(f"wrote {out}; understates={cmp.understates}")


if __name__ == "__main__":
    main()
