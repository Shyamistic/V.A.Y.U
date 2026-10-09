"""A2 regression: six-term cost identity and net_cost roll-up."""

from __future__ import annotations

import pytest

from monsoon_grid.cli import execute_replay


@pytest.fixture(scope="module")
def execution():
    return execute_replay(seed=20260701, policy_mode="balanced")


def test_per_slot_six_term_cost_identity(execution):
    for state in execution.twin.slot_states:
        expected = (
            state.grid_import_cost_usd
            + state.dr_cost_usd
            + state.market_shortfall_penalty_usd
            - state.market_revenue_usd
            - state.nonmarket_export_valuation_usd
        )
        assert state.cost_usd == pytest.approx(expected, abs=1e-6), state.slot_index


def test_net_cost_rolls_up_from_slots(execution):
    metrics = {m.name: m.value for m in execution.twin.metrics.values}
    total = sum(state.cost_usd for state in execution.twin.slot_states)
    assert metrics["net_cost_usd"] == pytest.approx(total, abs=1e-6)


def test_priced_dr_carries_cost(execution):
    metrics = {m.name: m.value for m in execution.twin.metrics.values}
    # Pricing DR must make shed carry cost (no longer free).
    if metrics["dr_shed_mwh"] > 0:
        assert metrics["dr_cost_usd"] > 0
