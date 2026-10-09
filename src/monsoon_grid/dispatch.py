"""Shared deterministic dispatch arithmetic for planner and fallback."""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

from .config import BESS_NUMERIC_TOLERANCE_MWH, DT_HOURS
from .contracts import BessSpec, EventRecord, LoadClassSpec, PortfolioFixture, ScenarioPath, SolarFarmSpec, TieLineSpec, WindFarmSpec


def partition_unserved(
    total_unserved_mwh: float,
    loads: Mapping[str, float],
    load_specs: Sequence[LoadClassSpec],
) -> tuple[float, float]:
    """Allocate an energy deficit critical-first, then to non-critical load.

    ``loads`` is per-load-class MW for the slot; ``total_unserved_mwh`` is the slot
    deficit already converted with ``* DT_HOURS``.  Returns
    ``(critical_unserved_mwh, noncritical_unserved_mwh)``.
    """
    critical_capacity_mwh = sum(loads.get(spec.asset_id, 0.0) for spec in load_specs if spec.critical) * DT_HOURS
    critical = min(total_unserved_mwh, critical_capacity_mwh)
    noncritical = total_unserved_mwh - critical
    return critical, noncritical


def slot_supply_mw(
    *,
    renewable_dispatch_mw: float,
    discharge_mw: float,
    import_mw: float,
    shed_mw: float,
    shift_down_mw: float,
    charge_mw: float,
    export_mw: float,
    shift_up_mw: float,
    rebound_mw: float,
) -> float:
    """Net slot supply used by the twin and the validator balance residual."""
    return (
        renewable_dispatch_mw
        + discharge_mw
        + import_mw
        + shed_mw
        + shift_down_mw
        - charge_mw
        - export_mw
        - shift_up_mw
        - rebound_mw
    )


def event_active(events: Iterable[EventRecord], event_type: str, slot: int) -> bool:
    return any(event.event_type == event_type and event.start_slot <= slot <= event.end_slot for event in events)


def event_detail(events: Iterable[EventRecord], event_type: str, slot: int, key: str, default: float) -> float:
    values = [event.details.get(key) for event in events if event.event_type == event_type and event.start_slot <= slot <= event.end_slot]
    return float(values[-1]) if values and isinstance(values[-1], (int, float)) else default


def effective_tie_capacity(portfolio: PortfolioFixture, events: Iterable[EventRecord], slot: int) -> float:
    tie = next(asset for asset in portfolio.assets if isinstance(asset, TieLineSpec))
    if event_active(events, "tie_derate", slot):
        return min(tie.capacity_mw * (1.0 - tie.derate_fraction), event_detail(events, "tie_derate", slot, "tie_capacity_mw", tie.capacity_mw))
    return tie.capacity_mw * (1.0 - tie.derate_fraction)


def available_generation(portfolio: PortfolioFixture, path: ScenarioPath, events: Iterable[EventRecord], slot: int) -> dict[str, float]:
    cloud = 0.45 if event_active(events, "monsoon_cloud", slot) else 1.0
    unavailable = {asset_id for event in events if event.event_type == "bess_outage" and event.start_slot <= slot <= event.end_slot for asset_id in event.affected_ids}
    result: dict[str, float] = {}
    for asset in sorted([*portfolio.assets_of("solar_farm"), *portfolio.assets_of("wind_farm")], key=lambda item: item.asset_id):
        if asset.asset_id in unavailable or not asset.enabled or asset.outage.status == "unavailable":
            result[asset.asset_id] = 0.0
            continue
        value = path.renewable_available_mw[asset.asset_id][slot]
        if isinstance(asset, SolarFarmSpec):
            value *= cloud
        result[asset.asset_id] = round(max(0.0, min(asset.capacity_mw * asset.availability_fraction, value)), 6)
    return result


def demand_by_load(portfolio: PortfolioFixture, path: ScenarioPath, events: Iterable[EventRecord], slot: int) -> dict[str, float]:
    multiplier = 1.25 if event_active(events, "demand_spike", slot) else 1.0
    return {asset.asset_id: round(path.demand_mw[asset.asset_id][slot] * multiplier, 6) for asset in sorted(portfolio.assets_of("load_class"), key=lambda item: item.asset_id)}


def safe_bess_discharge(energy_mwh: float, bess: BessSpec, requested_mw: float, available: bool = True) -> float:
    if not available:
        return 0.0
    energy_margin = max(0.0, energy_mwh - bess.reserve_mwh)
    max_by_energy = energy_margin * bess.eta_d / DT_HOURS
    return round(max(0.0, min(requested_mw, bess.discharge_rate_mw, max_by_energy)), 6)


def next_energy(energy_mwh: float, charge_mw: float, discharge_mw: float, bess: BessSpec) -> float:
    """Use the canonical BESS transition shared by planner, validator, and twin."""
    from .equations import bess_energy_next
    return bess_energy_next(energy_mwh, charge_mw, discharge_mw, bess)


def default_energy(portfolio: PortfolioFixture) -> dict[str, float]:
    return {asset.asset_id: asset.initial_energy_mwh for asset in portfolio.assets if isinstance(asset, BessSpec)}
