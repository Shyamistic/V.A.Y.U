"""A1 shared dispatch helpers: partition_unserved and slot_supply_mw."""

from __future__ import annotations

import pytest

from monsoon_grid.config import DT_HOURS
from monsoon_grid.dispatch import partition_unserved, slot_supply_mw
from monsoon_grid.fixtures import load_demo_portfolio


def _load_specs():
    return [a for a in load_demo_portfolio().assets if a.kind == "load_class"]


def test_partition_all_critical_first():
    specs = _load_specs()
    loads = {s.asset_id: (20.0 if s.critical else 10.0) for s in specs}
    crit_cap = sum(loads[s.asset_id] for s in specs if s.critical) * DT_HOURS
    # Deficit smaller than critical capacity: all critical.
    crit, noncrit = partition_unserved(crit_cap * 0.5, loads, specs)
    assert abs(crit - crit_cap * 0.5) < 1e-9
    assert noncrit == 0.0


def test_partition_overflow_to_noncritical():
    specs = _load_specs()
    loads = {s.asset_id: (20.0 if s.critical else 10.0) for s in specs}
    crit_cap = sum(loads[s.asset_id] for s in specs if s.critical) * DT_HOURS
    noncrit_cap = sum(loads[s.asset_id] for s in specs if not s.critical) * DT_HOURS
    total = crit_cap + noncrit_cap * 0.5
    crit, noncrit = partition_unserved(total, loads, specs)
    assert abs(crit - crit_cap) < 1e-9
    assert abs(noncrit - noncrit_cap * 0.5) < 1e-9


def test_partition_zero_demand():
    specs = _load_specs()
    loads = {s.asset_id: 0.0 for s in specs}
    crit, noncrit = partition_unserved(0.0, loads, specs)
    assert crit == 0.0 and noncrit == 0.0


def test_slot_supply_mw_sign_convention():
    supply = slot_supply_mw(
        renewable_dispatch_mw=10.0,
        discharge_mw=5.0,
        import_mw=3.0,
        shed_mw=1.0,
        shift_down_mw=2.0,
        charge_mw=4.0,
        export_mw=1.0,
        shift_up_mw=2.0,
        rebound_mw=1.0,
    )
    assert supply == pytest.approx(10 + 5 + 3 + 1 + 2 - 4 - 1 - 2 - 1)
