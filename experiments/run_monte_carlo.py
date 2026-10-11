r"""Multi-day Monte Carlo: orchestrator vs greedy_p50 vs no_battery (F1).

Runs N seeded days through the real production decision loop (orchestrator,
demo_overrides=False) and the two baselines, then reports per-operator mean KPIs
and PAIRED mean differences with a 95% CI (scipy.stats). Writes a
content-addressed, provenance-labelled artifact under
experiments/results/monte_carlo/ via the shared ExperimentRun harness. Wins AND
losses are published (orchestrator_loses_on); nothing is suppressed.

Command:
    & $py .\experiments\run_monte_carlo.py --config .\experiments\configs\monte_carlo.json
"""

from __future__ import annotations

import argparse
from pathlib import Path

from _harness import ExperimentRun, load_config

from monsoon_grid.monte_carlo import (
    build_monte_carlo_result,
    derive_day_seeds,
    run_operator_day,
    validate_monte_carlo_config,
)

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg = validate_monte_carlo_config(load_config(args.config))

    operators = list(cfg["operators"])
    policy_mode = cfg.get("policy_mode", "balanced")
    n_days = int(cfg["n_days"])

    run = ExperimentRun(
        experiment="monte_carlo",
        config=cfg,
        seed=cfg["seed"],
        dataset_fingerprints={"demo_portfolio": "self_generated"},
        total_budget_hours=float(cfg.get("total_budget_hours", 1.0)),
        results_root=REPO / "experiments" / "results",
    )
    day_seeds = derive_day_seeds(run, n_days)

    per_operator_per_day: dict[str, list[dict[str, float]]] = {op: [] for op in operators}
    days_run = 0
    for index, seed in enumerate(day_seeds):
        if run.budget_exhausted():
            run.budget_truncated = True
            run.unrun_cells.extend([f"day:{d}:seed:{day_seeds[d]}" for d in range(index, n_days)])
            break
        for operator in operators:
            per_operator_per_day[operator].append(run_operator_day(operator, seed, policy_mode=policy_mode))
        days_run += 1

    effective_days = days_run
    effective_seeds = day_seeds[:days_run]
    body = build_monte_carlo_result(
        run,
        operators=operators,
        policy_mode=policy_mode,
        n_days=effective_days,
        day_seeds=effective_seeds,
        per_operator_per_day=per_operator_per_day,
    )
    for key, value in body.items():
        run.record(key, value)
    out = run.write()
    print(f"wrote {out} (days_run={effective_days}, truncated={run.budget_truncated})")


if __name__ == "__main__":
    main()
