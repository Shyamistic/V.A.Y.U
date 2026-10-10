"""Tests for the agentic objective-selection layer (P4 F2).

Covers: situation featurization, autonomous ordering choice with a forced safety
tier, the reflective loop, regret-vs-preset, forbidden-tool blocking, and the
determinism of the deterministic brain.
"""

from __future__ import annotations

from monsoon_grid.agent.objective_agent import (
    KNOWN_OBJECTIVES,
    SAFETY_TIER,
    DeterministicBrain,
    ObjectiveSelectionAgent,
    build_brain,
    sanitize_order,
)
from monsoon_grid.dispatch import available_generation, demand_by_load
from monsoon_grid.fixtures import load_demo_events, load_demo_portfolio
from monsoon_grid.forecasts import build_forecasts
from monsoon_grid.scenarios import generate_scenarios


def _series():
    portfolio = load_demo_portfolio()
    events = load_demo_events()
    path = generate_scenarios(portfolio, seed=portfolio.seed).paths[1]
    gen: dict[str, list[float]] = {}
    dem: dict[str, list[float]] = {}
    for slot in range(96):
        for key, value in available_generation(portfolio, path, events, slot).items():
            gen.setdefault(key, [0.0] * 96)[slot] = value
        for key, value in demand_by_load(portfolio, path, events, slot).items():
            dem.setdefault(key, [0.0] * 96)[slot] = value
    return portfolio, events, path, gen, dem, build_forecasts(portfolio)


def test_sanitize_order_forces_safety_tier_first_and_is_total():
    order = sanitize_order(["net_cost", "carbon", "unknown_objective", "net_cost"])
    assert order[0] in SAFETY_TIER
    # no duplicates, no unknowns
    assert len(order) == len(set(order))
    assert all(name in KNOWN_OBJECTIVES for name in order)
    # total over the balanced objective set so the lexicographic solve is complete
    assert set(order) >= set(["net_cost", "carbon", "curtailment", "bess_wear", "dr_activation"])


def test_deterministic_brain_is_deterministic():
    portfolio, events, path, gen, dem, fc = _series()
    agent = ObjectiveSelectionAgent(portfolio=portfolio, brain=DeterministicBrain())
    a = agent.deliberate(path, events, replay_id="t", slot_index=41, generation_mw=gen, demand_mw=dem, forecasts=fc, energy_state=None, timestamp_utc="2026-07-01T10:15:00Z")
    b = agent.deliberate(path, events, replay_id="t", slot_index=41, generation_mw=gen, demand_mw=dem, forecasts=fc, energy_state=None, timestamp_utc="2026-07-01T10:15:00Z")
    assert a.chosen_objective_order == b.chosen_objective_order
    assert a.digest == b.digest


def test_agent_never_demotes_safety_tier_across_all_slots():
    portfolio, events, path, gen, dem, fc = _series()
    agent = ObjectiveSelectionAgent(portfolio=portfolio, brain=DeterministicBrain())
    for slot in (0, 10, 30, 41, 44, 70, 95):
        decision = agent.deliberate(path, events, replay_id="t", slot_index=slot, generation_mw=gen, demand_mw=dem, forecasts=fc, energy_state=None, timestamp_utc="t", max_steps=1)
        assert decision.chosen_objective_order[0] in SAFETY_TIER
        # The agent must never do WORSE than the preset on critical reliability.
        assert decision.reliability_regret_mwh <= 1e-6


def test_agent_blocks_forbidden_tools():
    portfolio, events, path, gen, dem, fc = _series()
    agent = ObjectiveSelectionAgent(portfolio=portfolio, brain=DeterministicBrain())
    decision = agent.deliberate(path, events, replay_id="t", slot_index=20, generation_mw=gen, demand_mw=dem, forecasts=fc, energy_state=None, timestamp_utc="t", max_steps=1)
    for forbidden in ("write_setpoint", "submit_market_order", "approve_self", "scada_command", "relay_command"):
        assert forbidden in decision.forbidden_tools_blocked


def test_reflective_loop_records_steps():
    portfolio, events, path, gen, dem, fc = _series()
    agent = ObjectiveSelectionAgent(portfolio=portfolio, brain=DeterministicBrain())
    decision = agent.deliberate(path, events, replay_id="t", slot_index=41, generation_mw=gen, demand_mw=dem, forecasts=fc, energy_state=None, timestamp_utc="t")
    assert len(decision.steps) >= 1
    assert decision.steps[0].iteration == 1
    assert decision.brain == "deterministic"


def test_agent_surfaces_concrete_action_cluster():
    portfolio, events, path, gen, dem, fc = _series()
    agent = ObjectiveSelectionAgent(portfolio=portfolio, brain=DeterministicBrain())
    decision = agent.deliberate(path, events, replay_id="t", slot_index=41, generation_mw=gen, demand_mw=dem, forecasts=fc, energy_state=None, timestamp_utc="t", max_steps=1)
    # The agent must take actions across all four P4 categories.
    categories = {a.category for a in decision.actions}
    assert categories == {"battery", "market", "renewable", "demand"}
    # Every action is attributed to a known objective and carries a conflict note.
    for action in decision.actions:
        assert action.driver_objective
        assert action.note
    assert decision.conflict_note


def test_build_brain_defaults_to_deterministic():
    assert build_brain().name == "deterministic"
    assert build_brain("deterministic").name == "deterministic"
