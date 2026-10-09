from monsoon_grid.config import FALLBACK_ALGORITHM_VERSION
from monsoon_grid.fallback import fallback_plan
from monsoon_grid.fixtures import load_demo_events, load_demo_portfolio
from monsoon_grid.planner import plan_next
from monsoon_grid.scenarios import generate_scenarios
from monsoon_grid.validator import validate_proposal


def test_milp_or_fallback_is_validated():
    portfolio = load_demo_portfolio(); events = load_demo_events(); scenarios = generate_scenarios(portfolio)
    result = plan_next(portfolio, scenarios.paths[1], events, replay_id="r", slot_index=0, policy_mode="balanced")
    decision = validate_proposal(portfolio, scenarios.paths[1], events, result.proposal, replay_id="r", slot_index=0)
    assert result.solver_result.status in {"OPTIMAL", "STDLIB_FALLBACK", "UNAVAILABLE", "TIME_LIMIT", "ERROR"}
    assert decision.status == "ACCEPTED"


def test_fallback_version_and_timeout_reason():
    portfolio = load_demo_portfolio(); events = load_demo_events(); path = generate_scenarios(portfolio).paths[1]
    proposal, solver, ties = fallback_plan(portfolio, path, events, replay_id="r", slot_index=0, policy_mode="reliability_first", reason="FALLBACK_BUDGET_EXHAUSTED")
    assert solver.solver_version == FALLBACK_ALGORITHM_VERSION
    assert solver.fallback_reason == "FALLBACK_BUDGET_EXHAUSTED"
    assert "reserve_preserved" in ties
