"""Multi-day Monte Carlo evaluation of the orchestrator vs baselines (F1).

A single Monte Carlo "day" is one integer seed: ``generate_scenarios`` produces
the five robust scenario paths and selects the realized path; the orchestrator
replays the real production decision loop over the full scenario set via
``cli.replay_twin(..., demo_overrides=False)`` (so NO slot-41/42 demo override
rides into the published result). N days are N distinct seeds derived
deterministically from one config seed through the experiment harness's seeded
PCG64 generator.

Three operators are compared on the SAME day and events:
  * ``orchestrator`` — the real agentic planner (scenario-robust MILP).
  * ``greedy_p50``   — the existing rule-based baseline.
  * ``no_battery``   — the greedy baseline with every BESS zeroed.

Reporting is a paired mean difference with a Student-t 95% CI per
``(baseline, metric)`` pair (``scipy.stats.ttest_rel`` / ``scipy.stats.t.ppf``),
with a ``std == 0`` degenerate-CI guard, and a top-level ``orchestrator_loses_on``
list enumerating every pair where a baseline beats the orchestrator. Nothing is
filtered — wins AND losses are reported (hard honesty rule).

scipy is required (locked ``scipy==1.15.1``); there is deliberately no NumPy-only
fallback. All randomness flows from the seeded harness; no global RNG is used.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from .assumptions import load_carbon_factor
from .config import POLICY_OBJECTIVES, REPLAY_BUDGET_SECONDS

KNOWN_OPERATORS = ("orchestrator", "greedy_p50", "no_battery")

# KPIs read directly from each operator's TwinResult.metrics. Reliability is
# reported as the PAIR (critical_unserved_mwh, reserve_shortfall_mwh).
MC_METRICS = (
    "net_cost_usd",
    "critical_unserved_mwh",
    "renewable_curtailment_mwh",
    "carbon_kgco2e",
    "reserve_shortfall_mwh",
)

# Every MC metric is "lower is better", so a positive paired mean difference
# (baseline - orchestrator) means the orchestrator is better on that metric.


def validate_monte_carlo_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate the Monte Carlo config, raising ValueError with an exact message.

    Enforced at config load, before any ExperimentRun is constructed, so no
    out-of-range value can reach the runner regardless of call order.
    """
    if "seed" not in config or not isinstance(config["seed"], int):
        raise ValueError("monte carlo requires an integer seed")
    n_days = config.get("n_days")
    if not isinstance(n_days, int):
        raise ValueError("monte carlo requires an integer n_days")
    if n_days < 2:
        raise ValueError("a Monte Carlo study needs at least two paired days")
    if n_days > 100:
        raise ValueError("n_days capped at 100 to bound wall-clock")
    operators = config.get("operators")
    if not operators or not isinstance(operators, list):
        raise ValueError("monte carlo requires a non-empty operators list")
    unknown = [op for op in operators if op not in KNOWN_OPERATORS]
    if unknown:
        raise ValueError(f"unknown monte carlo operator(s): {sorted(set(unknown))}")
    if "orchestrator" not in operators:
        raise ValueError("monte carlo requires the orchestrator operator")
    if not any(op in operators for op in ("greedy_p50", "no_battery")):
        raise ValueError("monte carlo requires at least one baseline to compare against")
    policy_mode = config.get("policy_mode", "balanced")
    if policy_mode not in POLICY_OBJECTIVES:
        raise ValueError(f"unknown policy_mode: {policy_mode}")
    budget = config.get("total_budget_hours", 1.0)
    if not isinstance(budget, (int, float)) or budget <= 0:
        raise ValueError("total_budget_hours must be a positive number")
    return config


def derive_day_seeds(run, n_days: int) -> list[int]:
    """Derive N deterministic day seeds from the single config seed via the
    harness's named PCG64 generator (no global RNG)."""
    gen = run.generators(["day_seeds"])["day_seeds"]
    return [int(s) for s in gen.integers(0, 2**31 - 1, size=n_days)]


def paired_comparison(baseline_values: list[float], orchestrator_values: list[float]) -> dict[str, Any]:
    """Paired mean difference with a Student-t 95% CI for one (baseline, metric).

    ``d_i = baseline_i - orchestrator_i``; positive mean => orchestrator better
    (every MC metric is lower-is-better). On a degenerate zero-variance sample
    the CI collapses to the mean, ``t_stat = 0.0`` and ``p_value = 1.0`` (never
    NaN). ``orchestrator_wins`` is taken from the sign of the mean difference.
    """
    from scipy.stats import t as t_dist, ttest_rel

    diffs = np.asarray(baseline_values, dtype=float) - np.asarray(orchestrator_values, dtype=float)
    n = int(diffs.size)
    mean_difference = float(np.mean(diffs))
    std = float(np.std(diffs, ddof=1)) if n > 1 else 0.0
    if std == 0.0 or n < 2:
        # Degenerate: identical paired differences => zero-width CI, no NaN leak.
        ci_low = ci_high = mean_difference
        t_stat = 0.0
        p_value = 1.0
    else:
        se = std / np.sqrt(n)
        crit = float(t_dist.ppf(0.975, n - 1))
        ci_low = mean_difference - crit * se
        ci_high = mean_difference + crit * se
        stat, pval = ttest_rel(baseline_values, orchestrator_values)
        t_stat = float(stat)
        p_value = float(pval)
    return {
        "mean_difference": round(mean_difference, 6),
        "ci95_low": round(ci_low, 6),
        "ci95_high": round(ci_high, 6),
        "t_stat": round(t_stat, 6),
        "p_value": round(p_value, 6),
        "orchestrator_wins": mean_difference > 0,
    }


def run_operator_day(operator: str, seed: int, *, policy_mode: str, deadline_seconds: float = REPLAY_BUDGET_SECONDS) -> dict[str, float]:
    """Run one operator for one seeded day; return its MC KPI dict.

    All three operators go through the same validate_proposal / run_twin KPI path.
    The orchestrator uses the clean replay loop with demo_overrides=False.
    """
    from .cli import replay_twin
    from .fixtures import fixture_set_hash, load_demo_events, load_demo_portfolio
    from .baseline import run_greedy_p50_baseline, run_no_battery_baseline
    from .scenarios import generate_scenarios

    carbon_factor = load_carbon_factor()
    portfolio = load_demo_portfolio().model_copy(update={"seed": seed, "fixture_set_hash": fixture_set_hash()})
    scenarios = generate_scenarios(portfolio, seed=seed)
    path = next(item for item in scenarios.paths if item.scenario_id == scenarios.realized_path_id)
    replay_id = f"mc-{operator}-{seed}"
    events = [event.model_copy(update={"replay_id": replay_id}) for event in load_demo_events()]

    if operator == "orchestrator":
        loop = replay_twin(
            portfolio,
            scenarios,
            events,
            replay_id=replay_id,
            policy_mode=policy_mode,
            carbon_factor=carbon_factor,
            deadline=time.monotonic() + min(float(deadline_seconds), REPLAY_BUDGET_SECONDS),
            demo_overrides=False,
        )
        metrics = loop.twin.metrics
    elif operator == "greedy_p50":
        metrics = run_greedy_p50_baseline(portfolio, path, events, replay_id=replay_id, carbon_factor=carbon_factor).metrics
    elif operator == "no_battery":
        metrics = run_no_battery_baseline(portfolio, path, events, replay_id=replay_id, carbon_factor=carbon_factor).metrics
    else:  # pragma: no cover - validated upstream
        raise ValueError(f"unknown operator: {operator}")

    values = {metric.name: metric.value for metric in metrics.values}
    return {name: float(values.get(name) or 0.0) for name in MC_METRICS}


def build_monte_carlo_result(
    run,
    *,
    operators: list[str],
    policy_mode: str,
    n_days: int,
    day_seeds: list[int],
    per_operator_per_day: dict[str, list[dict[str, float]]],
) -> dict[str, Any]:
    """Assemble the top-level artifact body from already-run per-day KPIs.

    Emits per_operator_means, per_operator_per_day, paired_comparisons and
    orchestrator_loses_on at the TOP LEVEL (workspace_evidence strips 'config'
    and 'environment' before the UI sees it, so the Science render reads only
    top-level fields)."""
    per_operator_means: dict[str, dict[str, float]] = {}
    for operator in operators:
        days = per_operator_per_day[operator]
        per_operator_means[operator] = {
            metric: round(float(np.mean([day[metric] for day in days])), 6) for metric in MC_METRICS
        }

    baselines = [op for op in operators if op != "orchestrator"]
    orch_days = per_operator_per_day["orchestrator"]
    paired_comparisons: dict[str, dict[str, Any]] = {}
    orchestrator_loses_on: list[list[str]] = []
    for baseline in baselines:
        base_days = per_operator_per_day[baseline]
        paired_comparisons[baseline] = {}
        for metric in MC_METRICS:
            comparison = paired_comparison(
                [day[metric] for day in base_days],
                [day[metric] for day in orch_days],
            )
            paired_comparisons[baseline][metric] = comparison
            if not comparison["orchestrator_wins"]:
                orchestrator_loses_on.append([baseline, metric])

    return {
        "n_days": n_days,
        "seed": run.seed,
        "day_seeds": day_seeds,
        "operators": operators,
        "policy_mode": policy_mode,
        "metrics": list(MC_METRICS),
        "per_operator_means": per_operator_means,
        "per_operator_per_day": per_operator_per_day,
        "paired_comparisons": paired_comparisons,
        "orchestrator_loses_on": orchestrator_loses_on,
        "note": (
            "Tier-C synthetic multi-day study on the demo portfolio and demo disturbance script; "
            "seeded, deterministic; reports wins AND losses; no real-plant skill claim."
        ),
    }
