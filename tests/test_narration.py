"""B5: the narration service describes planner/validator output without agency."""

from __future__ import annotations

from monsoon_grid.fixtures import load_demo_events, load_demo_portfolio
from monsoon_grid.narration import (
    DISCLAIMER,
    AgentRole,
    DecisionNarration,
    DecisionNarrator,
)
from monsoon_grid.planner import plan_next
from monsoon_grid.scenarios import generate_scenarios
from monsoon_grid.validator import validate_proposal


def _narrate(slot_index, events, policy_mode):
    portfolio = load_demo_portfolio()
    scenarios = generate_scenarios(portfolio)
    path = scenarios.paths[1]
    plan_result = plan_next(portfolio, path, events, replay_id="test-narration", slot_index=slot_index, policy_mode=policy_mode, scenario_paths=scenarios.paths)
    decision = validate_proposal(portfolio, path, events, plan_result.proposal, replay_id="test-narration", slot_index=slot_index)
    narrator = DecisionNarrator(portfolio)
    return narrator.narrate_decision(path, events, replay_id="test-narration", slot_index=slot_index, policy_mode=policy_mode, plan_result=plan_result, safety_decision=decision)


def test_narration_describes_normal_slot():
    narration = _narrate(10, [], "balanced")
    assert isinstance(narration, DecisionNarration)
    assert len(narration.thoughts) == 5
    roles = [t.role for t in narration.thoughts]
    for role in (AgentRole.WEATHER_NOWCASTER, AgentRole.MARKET_ARBITRAGEUR, AgentRole.ASSET_HEALTH_CUSTODIAN, AgentRole.GRID_STABILITY_GOVERNOR, AgentRole.CHIEF_ORCHESTRATOR):
        assert role in roles
    assert narration.digest != ""
    assert DISCLAIMER in narration.consensus_rationale


def test_narration_reflects_stress_events():
    demo_events = load_demo_events()
    stress_events = [e for e in demo_events if e.slot_index == 36]
    narration = _narrate(36, stress_events, "reliability_first")
    storage = next(t for t in narration.thoughts if t.role == AgentRole.ASSET_HEALTH_CUSTODIAN)
    assert storage.observed_context["bess_b_available"] is False
    grid = next(t for t in narration.thoughts if t.role == AgentRole.GRID_STABILITY_GOVERNOR)
    assert grid.observed_context["tie_derated"] is True


def test_thoughts_carry_observed_context_not_recommended_boundary():
    narration = _narrate(10, [], "balanced")
    for thought in narration.thoughts:
        assert hasattr(thought, "observed_context")
        assert not hasattr(thought, "recommended_boundary")
