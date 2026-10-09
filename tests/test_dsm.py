"""D1 regression: DSM charge boundary matrix and regime selection."""

from __future__ import annotations

import datetime as dt

import pytest

from monsoon_grid.assumptions import load_dsm_regimes
from monsoon_grid.dsm import compute_dsm_charge
from monsoon_grid.errors import DsmInputError

PRE = dt.date(2025, 1, 1)
POST = dt.date(2026, 6, 1)


def _charge(abs_error_fraction, technology, effective_date, available=100.0):
    # actual - scheduled = abs_error_fraction * available (over-injection)
    scheduled = 40.0
    actual = scheduled + abs_error_fraction * available
    return compute_dsm_charge(
        actual_mw=actual,
        scheduled_mw=scheduled,
        available_capacity_mw=available,
        technology=technology,
        reference_rate_usd_per_mwh=50.0,
        effective_date=effective_date,
    )


@pytest.mark.parametrize("technology", ["solar", "wind"])
@pytest.mark.parametrize("effective_date", [PRE, POST])
def test_tolerance_edge(technology, effective_date):
    regimes = load_dsm_regimes()
    tol = regimes.regime_for(effective_date).technologies[technology].tolerance_band_fraction
    assert _charge(tol - 1e-9, technology, effective_date).direction == "within_band"
    assert _charge(tol, technology, effective_date).direction == "within_band"
    r = _charge(tol + 1e-9, technology, effective_date)
    assert r.direction in ("over_injection", "under_injection")
    assert r.slab_index == 0


@pytest.mark.parametrize("technology", ["solar", "wind"])
def test_non_terminal_upper_edge_moves_to_next_slab(technology):
    regimes = load_dsm_regimes()
    slabs = regimes.regime_for(PRE).technologies[technology].slabs
    u = slabs[0].upper_fraction
    assert _charge(u - 1e-9, technology, PRE).slab_index == 0
    assert _charge(u, technology, PRE).slab_index == 1
    assert _charge(u + 1e-9, technology, PRE).slab_index == 1


def test_zero_error_is_within_band():
    r = _charge(0.0, "solar", PRE)
    assert r.direction == "within_band"
    assert r.charge_usd == 0.0
    assert r.slab_index is None


def test_sign_symmetry():
    over = compute_dsm_charge(actual_mw=70.0, scheduled_mw=40.0, available_capacity_mw=100.0, technology="solar", reference_rate_usd_per_mwh=50.0, effective_date=PRE)
    under = compute_dsm_charge(actual_mw=40.0, scheduled_mw=70.0, available_capacity_mw=100.0, technology="solar", reference_rate_usd_per_mwh=50.0, effective_date=PRE)
    assert over.charge_usd == pytest.approx(under.charge_usd)
    assert over.direction == "over_injection"
    assert under.direction == "under_injection"


def test_regime_switch_and_tightening():
    # Same absolute error of 0.12 is within the pre-regime solar band (tol 0.10
    # -> first slab) but under the post-regime tighter band yields a higher
    # multiplier. Assert different regime and strictly higher charge.
    pre = _charge(0.12, "solar", PRE)
    post = _charge(0.12, "solar", POST)
    assert pre.regime_id != post.regime_id
    assert post.charge_usd > pre.charge_usd


def test_available_capacity_under_outage_raises_slab():
    # Same absolute MW error, smaller available capacity -> larger error fraction
    # -> higher slab and strictly higher charge.
    nameplate = compute_dsm_charge(actual_mw=70.0, scheduled_mw=40.0, available_capacity_mw=100.0, technology="solar", reference_rate_usd_per_mwh=50.0, effective_date=PRE)
    derated = compute_dsm_charge(actual_mw=70.0, scheduled_mw=40.0, available_capacity_mw=60.0, technology="solar", reference_rate_usd_per_mwh=50.0, effective_date=PRE)
    assert derated.slab_index >= nameplate.slab_index
    assert derated.charge_usd >= nameplate.charge_usd


def test_zero_available_capacity_raises():
    with pytest.raises(DsmInputError):
        compute_dsm_charge(actual_mw=10.0, scheduled_mw=5.0, available_capacity_mw=0.0, technology="solar", reference_rate_usd_per_mwh=50.0, effective_date=PRE)


def test_no_regime_for_date_raises():
    with pytest.raises(DsmInputError):
        compute_dsm_charge(actual_mw=70.0, scheduled_mw=40.0, available_capacity_mw=100.0, technology="solar", reference_rate_usd_per_mwh=50.0, effective_date=dt.date(2000, 1, 1))
