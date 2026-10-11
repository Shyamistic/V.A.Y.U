"""F1: multi-day Monte Carlo — config rules, paired-stat math (incl. std==0
degenerate guard), determinism, demo-override isolation, and scenario-robustness.
"""

from __future__ import annotations

import time

import pytest

from monsoon_grid import monte_carlo as mc
from monsoon_grid.cli import replay_twin
from monsoon_grid.fixtures import fixture_set_hash, load_demo_events, load_demo_portfolio
from monsoon_grid.scenarios import generate_scenarios
from monsoon_grid.assumptions import load_carbon_factor
from monsoon_grid.config import REPLAY_BUDGET_SECONDS


def _base_config(**overrides):
    cfg = {
        "seed": 20260701,
        "n_days": 24,
        "operators": ["orchestrator", "greedy_p50", "no_battery"],
        "policy_mode": "balanced",
        "total_budget_hours": 1.0,
    }
    cfg.update(overrides)
    return cfg


# ---- validate_monte_carlo_config ------------------------------------------

def test_config_rejects_fewer_than_two_days():
    with pytest.raises(ValueError, match="at least two paired days"):
        mc.validate_monte_carlo_config(_base_config(n_days=1))


def test_config_rejects_more_than_hundred_days():
    with pytest.raises(ValueError, match="capped at 100"):
        mc.validate_monte_carlo_config(_base_config(n_days=101))


def test_config_requires_orchestrator():
    with pytest.raises(ValueError, match="requires the orchestrator operator"):
        mc.validate_monte_carlo_config(_base_config(operators=["greedy_p50", "no_battery"]))


def test_config_requires_a_baseline():
    with pytest.raises(ValueError, match="at least one baseline"):
        mc.validate_monte_carlo_config(_base_config(operators=["orchestrator"]))


def test_config_rejects_unknown_operator():
    with pytest.raises(ValueError, match="unknown monte carlo operator"):
        mc.validate_monte_carlo_config(_base_config(operators=["orchestrator", "psychic"]))


def test_config_accepts_valid():
    cfg = mc.validate_monte_carlo_config(_base_config())
    assert cfg["n_days"] == 24


# ---- paired-stat math ------------------------------------------------------

def test_paired_comparison_hand_computed():
    # baseline higher by exactly [2, 4, 6] over orchestrator => mean diff 4.0.
    baseline = [12.0, 14.0, 16.0]
    orchestrator = [10.0, 10.0, 10.0]
    result = mc.paired_comparison(baseline, orchestrator)
    assert result["mean_difference"] == pytest.approx(4.0, abs=1e-6)
    # positive mean => orchestrator better (lower is better).
    assert result["orchestrator_wins"] is True
    # CI brackets the mean and is non-degenerate.
    assert result["ci95_low"] < 4.0 < result["ci95_high"]
    assert result["p_value"] not in (None,)


def test_paired_comparison_std_zero_degenerate_guard():
    # Identical paired differences => zero variance; CI collapses, no NaN.
    baseline = [5.0, 5.0, 5.0]
    orchestrator = [2.0, 2.0, 2.0]
    result = mc.paired_comparison(baseline, orchestrator)
    assert result["mean_difference"] == pytest.approx(3.0, abs=1e-6)
    assert result["ci95_low"] == result["ci95_high"] == pytest.approx(3.0, abs=1e-6)
    assert result["t_stat"] == 0.0
    assert result["p_value"] == 1.0
    assert result["orchestrator_wins"] is True


def test_paired_comparison_reports_a_loss():
    # Baseline LOWER than orchestrator => orchestrator loses on this metric.
    baseline = [1.0, 1.0, 1.0]
    orchestrator = [5.0, 5.0, 5.0]
    result = mc.paired_comparison(baseline, orchestrator)
    assert result["mean_difference"] < 0
    assert result["orchestrator_wins"] is False


# ---- honesty: losses are surfaced -----------------------------------------

class _StubRun:
    seed = 20260701


def test_build_result_lists_losses_and_wins():
    operators = ["orchestrator", "greedy_p50"]
    per_day = {
        # greedy beats orchestrator on net_cost (lower), loses on curtailment.
        "orchestrator": [
            {"net_cost_usd": 100.0, "critical_unserved_mwh": 1.0, "renewable_curtailment_mwh": 5.0, "carbon_kgco2e": 10.0, "reserve_shortfall_mwh": 0.0},
            {"net_cost_usd": 110.0, "critical_unserved_mwh": 1.0, "renewable_curtailment_mwh": 6.0, "carbon_kgco2e": 11.0, "reserve_shortfall_mwh": 0.0},
        ],
        "greedy_p50": [
            {"net_cost_usd": 90.0, "critical_unserved_mwh": 2.0, "renewable_curtailment_mwh": 9.0, "carbon_kgco2e": 20.0, "reserve_shortfall_mwh": 0.0},
            {"net_cost_usd": 95.0, "critical_unserved_mwh": 2.0, "renewable_curtailment_mwh": 9.0, "carbon_kgco2e": 21.0, "reserve_shortfall_mwh": 0.0},
        ],
    }
    body = mc.build_monte_carlo_result(_StubRun(), operators=operators, policy_mode="balanced", n_days=2, day_seeds=[1, 2], per_operator_per_day=per_day)
    assert ["greedy_p50", "net_cost_usd"] in body["orchestrator_loses_on"]
    # orchestrator wins on curtailment (greedy higher), so NOT in losses.
    assert ["greedy_p50", "renewable_curtailment_mwh"] not in body["orchestrator_loses_on"]
    assert body["note"].startswith("Tier-C synthetic")
    # top-level fields present for the UI render.
    for key in ("n_days", "seed", "day_seeds", "per_operator_means", "paired_comparisons", "orchestrator_loses_on"):
        assert key in body


def test_day_seeds_are_deterministic():
    import numpy as np

    class _Run:
        seed = 20260701

        def generators(self, names):
            ss = np.random.SeedSequence(self.seed)
            children = ss.spawn(len(names))
            return {name: np.random.Generator(np.random.PCG64(child)) for name, child in zip(names, children)}

    first = mc.derive_day_seeds(_Run(), 24)
    second = mc.derive_day_seeds(_Run(), 24)
    assert first == second
    assert len(first) == 24
    assert all(isinstance(s, int) for s in first)


# ---- operator run + robustness --------------------------------------------

def test_run_operator_day_returns_all_mc_metrics():
    kpis = mc.run_operator_day("greedy_p50", 20260701, policy_mode="balanced")
    for metric in mc.MC_METRICS:
        assert metric in kpis
        assert isinstance(kpis[metric], float)


def test_orchestrator_day_is_deterministic():
    a = mc.run_operator_day("orchestrator", 20260701, policy_mode="balanced")
    b = mc.run_operator_day("orchestrator", 20260701, policy_mode="balanced")
    assert a == b


def test_replay_twin_uses_full_scenario_set(monkeypatch):
    """replay_twin must pass the FULL five-path set to plan_next, not one path."""
    import monsoon_grid.cli as cli_module

    seen_counts: list[int] = []
    original = cli_module.plan_next

    def spy(portfolio, path, events, **kwargs):
        seen_counts.append(len(list(kwargs.get("scenario_paths") or [])))
        return original(portfolio, path, events, **kwargs)

    monkeypatch.setattr(cli_module, "plan_next", spy)
    portfolio = load_demo_portfolio().model_copy(update={"seed": 20260701, "fixture_set_hash": fixture_set_hash()})
    scenarios = generate_scenarios(portfolio, seed=20260701)
    events = [e.model_copy(update={"replay_id": "rt"}) for e in load_demo_events()]
    cli_module.replay_twin(
        portfolio, scenarios, events,
        replay_id="rt", policy_mode="balanced", carbon_factor=load_carbon_factor(),
        deadline=time.monotonic() + REPLAY_BUDGET_SECONDS, demo_overrides=False,
    )
    assert seen_counts, "plan_next was never called"
    assert all(count == len(scenarios.paths) == 5 for count in seen_counts)


def test_demo_overrides_isolation():
    """An MC orchestrator day (demo_overrides=False) must not carry the slot-41
    hand-set dispatch or the slot-42 forced reliability_first policy."""
    portfolio = load_demo_portfolio().model_copy(update={"seed": 20260701, "fixture_set_hash": fixture_set_hash()})
    scenarios = generate_scenarios(portfolio, seed=20260701)
    events = [e.model_copy(update={"replay_id": "clean"}) for e in load_demo_events()]
    loop = replay_twin(
        portfolio, scenarios, events,
        replay_id="clean", policy_mode="balanced", carbon_factor=load_carbon_factor(),
        deadline=time.monotonic() + REPLAY_BUDGET_SECONDS, demo_overrides=False,
    )
    proposal_41 = loop.proposals.get(41)
    assert proposal_41 is not None
    # The slot-41 demo override sets grid_import to exactly 75.0 and bess-a
    # discharge to 8.0; the clean optimizer day must differ from that hack.
    forced = proposal_41.grid_import_mw == [75.0] and proposal_41.bess_discharge_mw.get("bess-a") == [8.0]
    assert not forced, "slot-41 demo override leaked into a clean MC day"
    # slot-42 policy must be the configured policy, not forced reliability_first.
    proposal_42 = loop.proposals.get(42)
    assert proposal_42 is not None
    assert proposal_42.policy_mode == "balanced"


def test_execute_replay_still_applies_demo_overrides():
    """The live demo path (demo_overrides=True) must keep the slot-41 hand-set
    values so the demo is byte-for-byte unchanged."""
    from monsoon_grid.cli import execute_replay

    execution = execute_replay(seed=20260701, policy_mode="balanced")
    proposal_41 = execution.proposals.get(41)
    assert proposal_41 is not None
    assert proposal_41.grid_import_mw == [75.0]
    assert proposal_41.bess_discharge_mw.get("bess-a") == [8.0]
