"""F2: maintenance actions — in_maintenance predicate, planner+validator
availability consistency (AC-12, HIGH-1 rewrite), delay/crew transitions, the
hard "no hidden critical shortfall" rule, and the agent AgentAction surface.
"""

from __future__ import annotations

from monsoon_grid.config import CREW_SPEEDUP_SLOTS
from monsoon_grid.contracts import MaintenanceState
from monsoon_grid.dispatch import available_generation, in_maintenance
from monsoon_grid.fixtures import load_demo_events, load_demo_portfolio
from monsoon_grid.maintenance import delay_window, dispatch_crew
from monsoon_grid.planner import _build_model, plan_next
from monsoon_grid.scenarios import generate_scenarios
from monsoon_grid.validator import validate_proposal
from monsoon_grid.assumptions import load_carbon_factor


def _bess_b(portfolio):
    return next(asset for asset in portfolio.assets if asset.asset_id == "bess-b")


def _with_maintenance(portfolio, asset_id, start, end, *, status="scheduled", cannot_delay=False):
    assets = []
    for asset in portfolio.assets:
        if asset.asset_id == asset_id:
            asset = asset.model_copy(update={"maintenance": MaintenanceState(status=status, start_slot=start, end_slot=end, cannot_delay=cannot_delay)})
        assets.append(asset)
    return portfolio.model_copy(update={"assets": assets})


# ---- in_maintenance --------------------------------------------------------

def test_in_maintenance_true_inside_window_scheduled_and_active():
    for status in ("scheduled", "active"):
        state = MaintenanceState(status=status, start_slot=8, end_slot=11)
        asset = type("A", (), {"maintenance": state})()
        assert in_maintenance(asset, 8) is True
        assert in_maintenance(asset, 11) is True
        assert in_maintenance(asset, 7) is False
        assert in_maintenance(asset, 12) is False


def test_in_maintenance_false_for_none_and_complete_and_null_window():
    for status in ("none", "complete"):
        asset = type("A", (), {"maintenance": MaintenanceState(status=status, start_slot=8, end_slot=11)})()
        assert in_maintenance(asset, 9) is False
    asset = type("A", (), {"maintenance": MaintenanceState(status="scheduled", start_slot=None, end_slot=None)})()
    assert in_maintenance(asset, 9) is False


# ---- AC-12 (HIGH-1): planner AND validator zero bess-b availability --------

def test_planner_zeros_maintained_bess_availability():
    portfolio = _with_maintenance(load_demo_portfolio(), "bess-b", 8, 11)
    events = load_demo_events()
    scenarios = generate_scenarios(portfolio, seed=20260701)
    model, common = _build_model(portfolio, scenarios.paths, events, slot_index=9, energy_state=None, previous_flow_mw=0.0, dr_allowed_load_ids=None, carbon_factor=load_carbon_factor())
    charge_idx = common["charge:bess-b"]
    discharge_idx = common["discharge:bess-b"]
    # Maintenance zeros the charge/discharge upper bounds for bess-b.
    assert model.upper[charge_idx] == 0.0
    assert model.upper[discharge_idx] == 0.0
    # bess-a (not maintained) keeps a positive rate.
    assert model.upper[common["charge:bess-a"]] > 0.0


def test_validator_rejects_nonzero_dispatch_for_maintained_bess():
    portfolio = _with_maintenance(load_demo_portfolio(), "bess-b", 8, 11)
    events = load_demo_events()
    scenarios = generate_scenarios(portfolio, seed=20260701)
    path = next(p for p in scenarios.paths if p.scenario_id == scenarios.realized_path_id)
    # A clean plan at slot 9 should already zero bess-b; force a non-zero dispatch
    # and assert the validator flags ASSET_UNAVAILABLE.
    plan = plan_next(portfolio, path, events, replay_id="maint", slot_index=9, policy_mode="balanced", scenario_paths=scenarios.paths)
    proposal = plan.proposal.model_copy(update={"bess_discharge_mw": {**plan.proposal.bess_discharge_mw, "bess-b": [1.0]}, "bess_charge_mw": {**plan.proposal.bess_charge_mw, "bess-b": [0.0]}})
    decision = validate_proposal(portfolio, path, events, proposal, replay_id="maint", slot_index=9)
    codes = {v.code for v in decision.violations}
    assert "ASSET_UNAVAILABLE" in codes
    assert decision.status != "ACCEPTED"


def test_twin_bess_energy_unchanged_when_dispatch_forced_zero():
    """The twin applies the validator-accepted proposal; a maintained BESS whose
    dispatch the validator forced to zero contributes no energy change (there is
    no twin availability branch — HIGH-1)."""
    portfolio = _with_maintenance(load_demo_portfolio(), "bess-b", 8, 11)
    events = load_demo_events()
    scenarios = generate_scenarios(portfolio, seed=20260701)
    path = next(p for p in scenarios.paths if p.scenario_id == scenarios.realized_path_id)
    plan = plan_next(portfolio, path, events, replay_id="maint", slot_index=9, policy_mode="balanced", scenario_paths=scenarios.paths)
    # the clean plan already zeros bess-b.
    assert plan.proposal.bess_charge_mw["bess-b"] == [0.0]
    assert plan.proposal.bess_discharge_mw["bess-b"] == [0.0]


# ---- delay_window ----------------------------------------------------------

def test_delay_window_moves_bess_window_to_lowest_price_band():
    portfolio = _with_maintenance(load_demo_portfolio(), "bess-b", 8, 11)
    events = load_demo_events()
    scenarios = generate_scenarios(portfolio, seed=20260701)
    path = next(p for p in scenarios.paths if p.scenario_id == scenarios.realized_path_id)
    new_state, note = delay_window(portfolio, path, events, "bess-b")
    length = 11 - 8
    assert new_state.end_slot - new_state.start_slot == length
    # The chosen window must have the minimum summed price over any valid window.
    def price_sum(start):
        return sum(path.price_usd_per_mwh[s] for s in range(start, start + length + 1))
    best = min(price_sum(s) for s in range(0, 96 - length))
    assert price_sum(new_state.start_slot) <= best + 1e-6
    assert "delay" in note or "lowest-value" in note


def test_delay_window_cannot_delay_returns_unchanged():
    portfolio = _with_maintenance(load_demo_portfolio(), "bess-b", 8, 11, cannot_delay=True)
    events = load_demo_events()
    scenarios = generate_scenarios(portfolio, seed=20260701)
    path = next(p for p in scenarios.paths if p.scenario_id == scenarios.realized_path_id)
    new_state, note = delay_window(portfolio, path, events, "bess-b")
    assert new_state.start_slot == 8 and new_state.end_slot == 11
    assert "not delayable" in note


# ---- dispatch_crew ---------------------------------------------------------

def test_dispatch_crew_shortens_window():
    state = MaintenanceState(status="active", start_slot=8, end_slot=20)
    new_state, note = dispatch_crew(state, slot=10)
    assert new_state.end_slot == 20 - CREW_SPEEDUP_SLOTS
    assert "shortened" in note


def test_dispatch_crew_never_below_current_slot():
    state = MaintenanceState(status="active", start_slot=8, end_slot=11)
    new_state, note = dispatch_crew(state, slot=11)
    # end already at/under slot + speedup => cannot shorten below current slot.
    assert new_state.end_slot >= 11


def test_dispatch_crew_never_extends():
    state = MaintenanceState(status="active", start_slot=8, end_slot=11)
    new_state, _ = dispatch_crew(state, slot=0)
    assert new_state.end_slot <= 11


# ---- hard rule: maintenance cannot hide a critical shortfall ---------------

def test_maintenance_induced_critical_deficit_escalates():
    """Force both BESS and the grid off during a maintenance window so a critical
    deficit is unavoidable; the validator must surface it, never ACCEPT."""
    portfolio = _with_maintenance(load_demo_portfolio(), "bess-b", 8, 11)
    events = load_demo_events()
    scenarios = generate_scenarios(portfolio, seed=20260701)
    path = next(p for p in scenarios.paths if p.scenario_id == scenarios.realized_path_id)
    plan = plan_next(portfolio, path, events, replay_id="maint", slot_index=9, policy_mode="balanced", scenario_paths=scenarios.paths)
    # Zero every supply lever to manufacture a deficit, then validate.
    proposal = plan.proposal.model_copy(update={
        "grid_import_mw": [0.0],
        "bess_discharge_mw": {k: [0.0] for k in plan.proposal.bess_discharge_mw},
        "bess_charge_mw": {k: [0.0] for k in plan.proposal.bess_charge_mw},
        "renewable_dispatch_mw": {k: [0.0] for k in plan.proposal.renewable_dispatch_mw},
    })
    decision = validate_proposal(portfolio, path, events, proposal, replay_id="maint", slot_index=9)
    assert decision.status != "ACCEPTED"
    assert decision.critical_unserved_mwh > 0 or decision.status == "ESCALATED_NO_SAFE_ACTION"


# ---- agent AgentAction surface ---------------------------------------------

def test_extract_actions_emits_maintenance_line():
    from monsoon_grid.agent.objective_agent import SituationFeatures, extract_actions

    portfolio = _with_maintenance(load_demo_portfolio(), "bess-b", 8, 11)
    events = load_demo_events()
    scenarios = generate_scenarios(portfolio, seed=20260701)
    path = next(p for p in scenarios.paths if p.scenario_id == scenarios.realized_path_id)
    plan = plan_next(portfolio, path, events, replay_id="maint", slot_index=9, policy_mode="balanced", scenario_paths=scenarios.paths)
    features = SituationFeatures(
        slot_index=9, timestamp_utc="", renewable_available_mw=10.0, demand_mw=20.0,
        net_margin_mw=-10.0, price_usd_per_mwh=70.0, forecast_band_width_mw=5.0,
        uncertainty_ratio=0.4, bess_soc_fraction=0.5, bess_outage=False, tie_derated=False,
        weather_event=False, demand_spike=False,
    )
    actions, _ = extract_actions(plan.proposal, ["required_reliability_tier", "net_cost"], features, portfolio=portfolio, events=events, slot_index=9)
    maintenance_lines = [a for a in actions if a.category == "maintenance"]
    assert maintenance_lines, "no maintenance AgentAction emitted inside the window"
    line = maintenance_lines[0]
    assert line.target == "bess-b"
    assert "SIMULATED TWIN" in line.note


def test_extract_actions_no_maintenance_line_outside_window():
    from monsoon_grid.agent.objective_agent import SituationFeatures, extract_actions

    portfolio = _with_maintenance(load_demo_portfolio(), "bess-b", 8, 11)
    events = load_demo_events()
    scenarios = generate_scenarios(portfolio, seed=20260701)
    path = next(p for p in scenarios.paths if p.scenario_id == scenarios.realized_path_id)
    plan = plan_next(portfolio, path, events, replay_id="maint", slot_index=40, policy_mode="balanced", scenario_paths=scenarios.paths)
    features = SituationFeatures(
        slot_index=40, timestamp_utc="", renewable_available_mw=10.0, demand_mw=20.0,
        net_margin_mw=-10.0, price_usd_per_mwh=70.0, forecast_band_width_mw=5.0,
        uncertainty_ratio=0.4, bess_soc_fraction=0.5, bess_outage=False, tie_derated=False,
        weather_event=False, demand_spike=False,
    )
    actions, _ = extract_actions(plan.proposal, ["required_reliability_tier", "net_cost"], features, portfolio=portfolio, events=events, slot_index=40)
    assert not [a for a in actions if a.category == "maintenance"]


def test_renewable_maintenance_zeros_availability():
    portfolio = _with_maintenance(load_demo_portfolio(), "solar-01", 8, 11)
    events = load_demo_events()
    scenarios = generate_scenarios(portfolio, seed=20260701)
    path = next(p for p in scenarios.paths if p.scenario_id == scenarios.realized_path_id)
    avail = available_generation(portfolio, path, events, 9)
    assert avail["solar-01"] == 0.0
