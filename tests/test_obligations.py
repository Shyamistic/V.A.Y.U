"""D2 regression: obligation attainment tracking and shadow-price heuristic."""

from __future__ import annotations

import pytest

from monsoon_grid.fixtures import load_demo_obligations, load_demo_portfolio
from monsoon_grid.obligations import ObligationTracker


def _tracker():
    return ObligationTracker(load_demo_obligations(), portfolio=load_demo_portfolio())


def _delivered(total_mwh, obligation):
    # Spread delivered ENERGY (MWh) evenly across the peak window slots; the
    # tracker consumes per-slot MWh directly.
    slots = obligation.peak_window_slots
    per_slot = total_mwh / len(slots) if slots else 0.0
    series = [0.0] * 96
    for s in slots:
        series[s] = per_slot
    return series


def test_comfortably_ahead_zero_price():
    tracker = _tracker()
    ob = tracker.obligations[0]
    last = ob.peak_window_slots[-1]
    # Deliver 110% of effective commitment by the end of the window.
    effective = ob.committed_value * (1 - ob.relaxation_allowance_fraction)
    series = _delivered(effective * 1.10, ob)
    attainments = tracker.update(last, series)
    a = attainments[0]
    assert a.projected_attainment_fraction >= 1.05
    assert a.obligation_shadow_price_usd_per_mwh == 0.0


def test_nothing_delivered_hits_penalty_rate():
    tracker = _tracker()
    ob = tracker.obligations[0]
    last = ob.peak_window_slots[-1]
    attainments = tracker.update(last, [0.0] * 96)
    a = attainments[0]
    assert a.obligation_shadow_price_usd_per_mwh == pytest.approx(ob.shortfall_penalty_usd_per_mwh, abs=1e-6)


def test_at_risk_price_below_penalty():
    tracker = _tracker()
    ob = tracker.obligations[0]
    last = ob.peak_window_slots[-1]
    effective = ob.committed_value * (1 - ob.relaxation_allowance_fraction)
    series = _delivered(effective * 0.40, ob)
    a = tracker.update(last, series)[0]
    assert 0.0 < a.obligation_shadow_price_usd_per_mwh < ob.shortfall_penalty_usd_per_mwh


def test_relaxation_reduces_effective_commitment():
    tracker = _tracker()
    ob = tracker.obligations[0]
    last = ob.peak_window_slots[-1]
    a = tracker.update(last, [0.0] * 96)[0]
    expected = ob.committed_value * (1 - ob.relaxation_allowance_fraction)
    assert a.effective_commitment_mwh == pytest.approx(expected, abs=1e-6)


def test_first_slot_elapsed_fraction():
    tracker = _tracker()
    ob = tracker.obligations[0]
    first = ob.peak_window_slots[0]
    a = tracker.update(first, [0.0] * 96)[0]
    assert a.elapsed_fraction_of_period == pytest.approx(1.0 / len(ob.peak_window_slots), abs=1e-6)


def test_portfolio_shadow_price_is_max():
    tracker = _tracker()
    ob = tracker.obligations[0]
    last = ob.peak_window_slots[-1]
    attainments = tracker.update(last, [0.0] * 96)
    assert ObligationTracker.portfolio_shadow_price(attainments) == max(a.obligation_shadow_price_usd_per_mwh for a in attainments)
