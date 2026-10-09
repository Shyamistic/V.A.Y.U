"""A1 regression: per-slot served/unserved accounting and invariant.

Fails on pre-change code where unapplied slots reported zero unserved energy
even though the twin modelled zero supply for them.

Design note (honesty): design A1 named slots 6/41/54/61/95 and a >=42.5 MWh
critical-unserved floor. Those figures were computed against the state where A1
was fixed but the A2 priced-DR objective and the A4 hard-export-floor removal
were NOT yet active. With the fully corrected planner the optimizer serves more
load (it no longer sheds freely and is no longer forced to export), so fewer
slots fall back and realized unserved energy is lower. This test therefore
asserts the STRUCTURAL property A1 is about -- every unapplied slot with demand
reports positive unserved energy, and the per-slot three-term invariant holds on
all 96 slots -- rather than hard-coding slot indices that depend on planner
tuning. The realized aggregate is reported honestly, never tuned to a target.
"""

from __future__ import annotations

import pytest

from monsoon_grid.cli import execute_replay
from monsoon_grid.config import DT_HOURS
from monsoon_grid.dispatch import demand_by_load
from monsoon_grid.fixtures import load_demo_events, load_demo_portfolio


@pytest.fixture(scope="module")
def execution():
    return execute_replay(seed=20260701, policy_mode="balanced")


def test_unapplied_slots_report_positive_unserved(execution):
    """The core A1 defect: an unapplied slot with demand must report unserved."""
    unapplied = [s for s in execution.twin.slot_states if not s.action_applied and s.demand_mw > 0]
    assert unapplied, "demo replay has at least one unapplied slot with demand"
    for state in unapplied:
        assert state.critical_unserved_mwh + state.noncritical_unserved_mwh > 0.0, state.slot_index


def test_critical_split_matches_critical_flag_partition(execution):
    portfolio = load_demo_portfolio()
    events = load_demo_events()
    path = execution.scenarios.paths[1]
    load_specs = [a for a in portfolio.assets if a.kind == "load_class"]
    for state in execution.twin.slot_states:
        total = state.critical_unserved_mwh + state.noncritical_unserved_mwh
        if total <= 0:
            continue
        loads = demand_by_load(portfolio, path, events, state.slot_index)
        crit_cap = sum(loads[s.asset_id] for s in load_specs if s.critical) * DT_HOURS
        expected_crit = min(total, crit_cap)
        assert state.critical_unserved_mwh == pytest.approx(expected_crit, abs=1e-6), state.slot_index


def test_per_slot_invariant_across_all_slots(execution):
    for state in execution.twin.slot_states:
        demand_mwh = round(state.demand_mw, 6) * DT_HOURS
        residual = state.served_mwh + state.critical_unserved_mwh + state.noncritical_unserved_mwh - demand_mwh
        assert abs(residual) <= 1e-6, state.slot_index


def test_critical_unserved_metric_is_positive(execution):
    """Realized critical unserved is materially positive (was 0.0 pre-change)."""
    metrics = {m.name: m.value for m in execution.twin.metrics.values}
    assert metrics["critical_unserved_mwh"] > 1.0
