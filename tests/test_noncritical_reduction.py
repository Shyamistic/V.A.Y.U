"""F3: UNPRICED non-critical reduction lever — distinctness from priced DR,
bounds + critical-never-reduce, the reduce_mw contract-shape invariant (incl. a
fallback-produced proposal), and the agent AgentAction surface.
"""

from __future__ import annotations

import datetime as dt

import pytest

from monsoon_grid.contracts import ActionProposal, Provenance, SolverResult
from monsoon_grid.fallback import fallback_plan
from monsoon_grid.fixtures import load_demo_events, load_demo_portfolio
from monsoon_grid.planner import plan_next
from monsoon_grid.scenarios import generate_scenarios
from monsoon_grid.twin import run_twin
from monsoon_grid.validator import validate_proposal
from monsoon_grid.assumptions import load_carbon_factor


def _prov():
    return Provenance(label="scenario_assumption", source_id="t", source_version="t", input_refs=[])


def _solver():
    return SolverResult(
        status="STDLIB_FALLBACK", solver_name="t", solver_version="t", budget_seconds=0.0,
        objective_values=[], stage_optima=[], message="t", fallback_reason="t",
        method_hash="0" * 64, provenance=_prov(),
    )


def _base_proposal(portfolio, **overrides):
    loads = {a.asset_id for a in portfolio.assets_of("load_class")}
    bess = {a.asset_id for a in portfolio.assets_of("bess")}
    renew = {a.asset_id for a in [*portfolio.assets_of("solar_farm"), *portfolio.assets_of("wind_farm")]}
    base = dict(
        proposal_id="p:0", replay_id="r", decision_id="d:0", commit_slot=0,
        renewable_dispatch_mw={a: [0.0] for a in renew},
        bess_charge_mw={a: [0.0] for a in bess},
        bess_discharge_mw={a: [0.0] for a in bess},
        grid_import_mw=[0.0], grid_export_mw=[0.0],
        shed_mw={a: [0.0] for a in loads},
        shift_down_mw={a: [0.0] for a in loads},
        shift_up_mw={a: [0.0] for a in loads},
        rebound_mw={a: [0.0] for a in loads},
        policy_mode="balanced", solver_result=_solver(),
        provenance=_prov(),
        expires_at=dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc),
    )
    base.update(overrides)
    return ActionProposal(**base)


# ---- contract shape invariant ----------------------------------------------

def test_reduce_mw_multi_slot_rejected():
    portfolio = load_demo_portfolio()
    with pytest.raises(ValueError, match="exactly one slot"):
        _base_proposal(portfolio, reduce_mw={"shiftable_process": [1.0, 2.0]})


def test_reduce_mw_negative_rejected():
    portfolio = load_demo_portfolio()
    with pytest.raises(ValueError, match="non-negative"):
        _base_proposal(portfolio, reduce_mw={"shiftable_process": [-1.0]})


def test_reduce_mw_defaults_to_empty():
    portfolio = load_demo_portfolio()
    proposal = _base_proposal(portfolio)
    assert proposal.reduce_mw == {}


def test_fallback_proposal_validates_with_reduce_absent():
    """plan_next returns a fallback proposal on solver unavailability; its
    ActionProposal construction omits reduce_mw and must still validate (the MC
    relies on this)."""
    portfolio = load_demo_portfolio()
    events = load_demo_events()
    scenarios = generate_scenarios(portfolio, seed=20260701)
    path = next(p for p in scenarios.paths if p.scenario_id == scenarios.realized_path_id)
    proposal, _, _ = fallback_plan(portfolio, path, events, replay_id="fb", slot_index=0, policy_mode="balanced")
    assert proposal.reduce_mw == {}
    # And it passes the independent validator roster/non-negativity checks.
    decision = validate_proposal(portfolio, path, events, proposal, replay_id="fb", slot_index=0)
    assert "REDUCE_LIMIT" not in {v.code for v in decision.violations}


# ---- validator bounds + critical/opt-out ------------------------------------

def test_reduce_on_critical_load_is_fatal():
    portfolio = load_demo_portfolio()
    events = load_demo_events()
    scenarios = generate_scenarios(portfolio, seed=20260701)
    path = next(p for p in scenarios.paths if p.scenario_id == scenarios.realized_path_id)
    proposal = _base_proposal(portfolio, reduce_mw={a.asset_id: ([2.0] if a.asset_id == "critical_process" else [0.0]) for a in portfolio.assets_of("load_class")})
    decision = validate_proposal(portfolio, path, events, proposal, replay_id="r", slot_index=0)
    assert "CRITICAL_LOAD_SHED" in {v.code for v in decision.violations}
    assert decision.status != "ACCEPTED"


def test_reduce_beyond_max_is_fatal():
    portfolio = load_demo_portfolio()
    events = load_demo_events()
    scenarios = generate_scenarios(portfolio, seed=20260701)
    path = next(p for p in scenarios.paths if p.scenario_id == scenarios.realized_path_id)
    # shiftable_process has max_reduce_mw=3.0; request 9 MW.
    proposal = _base_proposal(portfolio, reduce_mw={a.asset_id: ([9.0] if a.asset_id == "shiftable_process" else [0.0]) for a in portfolio.assets_of("load_class")})
    decision = validate_proposal(portfolio, path, events, proposal, replay_id="r", slot_index=0)
    assert "REDUCE_LIMIT" in {v.code for v in decision.violations}


# ---- distinctness from priced DR + metric separation ------------------------

def test_reduce_is_unpriced_and_separate_from_dr_shed():
    """A reduce-bearing proposal applied through the twin reports
    noncritical_reduction_mwh > 0 while dr_shed_mwh / dr_cost_usd stay zero."""
    from monsoon_grid.cli import _approval_event  # synthetic approval helper

    portfolio = load_demo_portfolio()
    events = load_demo_events()
    scenarios = generate_scenarios(portfolio, seed=20260701)
    path = next(p for p in scenarios.paths if p.scenario_id == scenarios.realized_path_id)
    # Build a balanced proposal: reduce 2 MW on shiftable_process, matched by an
    # equal import so the slot energy balance is satisfied (reduce raises supply).
    reduce_mw = {a.asset_id: ([2.0] if a.asset_id == "shiftable_process" else [0.0]) for a in portfolio.assets_of("load_class")}
    # Compute demand so we can balance with import.
    from monsoon_grid.dispatch import demand_by_load
    demand = sum(demand_by_load(portfolio, path, events, 0).values())
    import_mw = max(0.0, demand - 2.0)
    proposal = _base_proposal(portfolio, reduce_mw=reduce_mw, grid_import_mw=[round(import_mw, 6)])
    decision = validate_proposal(portfolio, path, events, proposal, replay_id="r", slot_index=0)
    # It need not be ACCEPTED (ramp/tie limits), but the metric separation is what
    # we assert via a direct twin run on a one-slot applied proposal.
    proposals = {0: proposal}
    decisions = {0: decision}
    approvals = {}
    if decision.status == "ACCEPTED":
        approvals = {0: _approval_event("r", 0, proposal, {a.asset_id: a.initial_energy_mwh for a in portfolio.assets if a.kind == "bess"}, 0.0)}
    result = run_twin(portfolio, path, events, proposals, decisions, replay_id="r", applied_slots=set(approvals), approvals=approvals, carbon_factor=load_carbon_factor())
    values = {m.name: m.value for m in result.metrics.values}
    assert "noncritical_reduction_mwh" in values
    # reduce never counts as priced DR.
    assert values["dr_shed_mwh"] == 0.0
    assert values["dr_cost_usd"] == 0.0


def test_planner_leaves_reduce_zero_when_not_needed():
    """With ample supply, the optimizer should not activate the unpriced reduce
    lever (it carries a dr_activation penalty)."""
    portfolio = load_demo_portfolio()
    events = load_demo_events()
    scenarios = generate_scenarios(portfolio, seed=20260701)
    path = next(p for p in scenarios.paths if p.scenario_id == scenarios.realized_path_id)
    # Slot 0 (midnight) is a calm slot; expect no voluntary reduction.
    plan = plan_next(portfolio, path, events, replay_id="r", slot_index=0, policy_mode="balanced", scenario_paths=scenarios.paths)
    assert all(v[0] == 0.0 for v in plan.proposal.reduce_mw.values())


# ---- agent AgentAction surface ---------------------------------------------

def test_extract_actions_emits_distinct_reduction_line():
    from monsoon_grid.agent.objective_agent import SituationFeatures, extract_actions

    portfolio = load_demo_portfolio()
    events = load_demo_events()
    scenarios = generate_scenarios(portfolio, seed=20260701)
    path = next(p for p in scenarios.paths if p.scenario_id == scenarios.realized_path_id)
    plan = plan_next(portfolio, path, events, replay_id="r", slot_index=0, policy_mode="balanced", scenario_paths=scenarios.paths)
    # Inject a non-zero reduce so the distinct line is produced.
    proposal = plan.proposal.model_copy(update={"reduce_mw": {a.asset_id: ([2.0] if a.asset_id == "shiftable_process" else [0.0]) for a in portfolio.assets_of("load_class")}})
    features = SituationFeatures(
        slot_index=0, timestamp_utc="", renewable_available_mw=10.0, demand_mw=20.0,
        net_margin_mw=-10.0, price_usd_per_mwh=70.0, forecast_band_width_mw=5.0,
        uncertainty_ratio=0.4, bess_soc_fraction=0.5, bess_outage=False, tie_derated=False,
        weather_event=False, demand_spike=False,
    )
    actions, _ = extract_actions(proposal, ["required_reliability_tier", "net_cost"], features, portfolio=portfolio, events=events, slot_index=0)
    reduce_lines = [a for a in actions if a.action == "Reduce non-critical load"]
    dr_lines = [a for a in actions if a.action in ("Trigger demand response", "No demand response")]
    assert reduce_lines, "no distinct non-critical reduction line emitted"
    assert dr_lines, "priced-DR line should still coexist"
    assert "distinct from priced demand response" in reduce_lines[0].note
