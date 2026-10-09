"""Shared DR, market, BESS, and tie-line equations."""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import BESS_NUMERIC_TOLERANCE_MWH, DT_HOURS
from .contracts import BessSpec, DRContract, GridInterfaceSpec, LoadClassSpec, PortfolioFixture, ScenarioPath


def bess_energy_next(energy_mwh: float, charge_mw: float, discharge_mw: float, bess: BessSpec) -> float:
    if charge_mw < -BESS_NUMERIC_TOLERANCE_MWH or discharge_mw < -BESS_NUMERIC_TOLERANCE_MWH:
        raise ValueError("BESS actions must be non-negative")
    if charge_mw > BESS_NUMERIC_TOLERANCE_MWH and discharge_mw > BESS_NUMERIC_TOLERANCE_MWH:
        raise ValueError("simultaneous charge/discharge")
    return round(energy_mwh + charge_mw * bess.eta_c * DT_HOURS - discharge_mw * DT_HOURS / bess.eta_d, 6)


def physical_flow(import_mw: float, export_mw: float) -> float:
    return round(import_mw - export_mw, 6)


def delivered_import_mwh(import_mw: float, loss_fraction: float) -> float:
    return round(import_mw * DT_HOURS * (1.0 - loss_fraction), 6)


def settled_export_mwh(export_mw: float, loss_fraction: float) -> float:
    return round(export_mw * DT_HOURS * (1.0 - loss_fraction), 6)


def shift_energy_mwh(shift_down_mw: list[float], shift_up_mw: list[float], deadline_slot: int) -> tuple[float, float]:
    down = sum(shift_down_mw[: deadline_slot + 1]) * DT_HOURS
    up = sum(shift_up_mw[: deadline_slot + 1]) * DT_HOURS
    return round(down, 6), round(up, 6)


def dr_deadline_is_conserved(contract: DRContract, shift_down_mw: list[float], shift_up_mw: list[float]) -> bool:
    if any(value < -BESS_NUMERIC_TOLERANCE_MWH for value in [*shift_down_mw, *shift_up_mw]):
        return False
    down, up = shift_energy_mwh(shift_down_mw, shift_up_mw, contract.shift_deadline_slot)
    return abs(down - up) <= BESS_NUMERIC_TOLERANCE_MWH


def rebound_within_contract(contract: DRContract, rebound_mw: list[float]) -> bool:
    if len(rebound_mw) != 96 or any(value < -BESS_NUMERIC_TOLERANCE_MWH for value in rebound_mw):
        return False
    return all(value <= contract.rebound_rate_mw + BESS_NUMERIC_TOLERANCE_MWH if slot in contract.rebound_window_slots else abs(value) <= BESS_NUMERIC_TOLERANCE_MWH for slot, value in enumerate(rebound_mw)) and sum(rebound_mw[slot] * DT_HOURS for slot in contract.rebound_window_slots) <= contract.contracted_rebound_mwh + BESS_NUMERIC_TOLERANCE_MWH


@dataclass(frozen=True)
class SlotCostRates:
    """The USD/MWh rates used identically by the planner objective and the twin metric."""

    import_usd_per_mwh: float
    committed_export_usd_per_mwh: float
    uncommitted_export_usd_per_mwh: float
    shortfall_usd_per_mwh: float
    shed_usd_per_mwh: dict[str, float] = field(default_factory=dict)
    shift_usd_per_mwh: dict[str, float] = field(default_factory=dict)


def slot_cost_rates(portfolio, path, commitments, settlement, flexibility, *, slot_index: int) -> SlotCostRates:
    """Construct the one set of slot cost rates shared by planner and twin.

    ``commitments`` is the portfolio's list of ``MarketCommitment``; ``settlement``
    is a ``MarketSettlementAssumption``; ``flexibility`` is a
    ``FlexibilityCostAssumption``.  The commitment used for export/shortfall rates
    is the one whose delivery window contains ``slot_index`` (at most one in the
    demo); when none is active those rates fall back to zero so an uncommitted
    export is valued only at the labelled non-market rate.
    """
    from .market import intent_from_commitment
    from .contracts import market_delivery_due

    grid: GridInterfaceSpec = next(asset for asset in portfolio.assets if isinstance(asset, GridInterfaceSpec))
    price = path.price_usd_per_mwh[slot_index]
    import_rate = price * (1.0 - grid.loss_fraction) + grid.fee_usd_per_mwh
    uncommitted_rate = settlement.nonmarket_export_rate_usd_per_mwh * (1.0 - grid.loss_fraction)

    active = None
    for commitment in sorted(commitments, key=lambda c: c.commitment_id):
        intent = intent_from_commitment(commitment)
        if market_delivery_due(intent, delivery_slot=slot_index):
            active = commitment
            break
    if active is not None:
        committed_rate = active.price_usd_per_mwh * (1.0 - active.loss_fraction) - active.fee_usd_per_mwh
        shortfall_rate = active.price_usd_per_mwh + active.shortfall_penalty_usd_per_mwh
    else:
        committed_rate = 0.0
        shortfall_rate = 0.0

    costs = {entry.load_id: entry for entry in flexibility.entries}
    load_ids = [asset.asset_id for asset in portfolio.assets if isinstance(asset, LoadClassSpec)]
    shed = {load_id: float(costs[load_id].shed_cost_usd_per_mwh) for load_id in load_ids if load_id in costs}
    shift = {load_id: float(costs[load_id].shift_cost_usd_per_mwh) for load_id in load_ids if load_id in costs}
    return SlotCostRates(
        import_usd_per_mwh=import_rate,
        committed_export_usd_per_mwh=committed_rate,
        uncommitted_export_usd_per_mwh=uncommitted_rate,
        shortfall_usd_per_mwh=shortfall_rate,
        shed_usd_per_mwh=shed,
        shift_usd_per_mwh=shift,
    )


__all__ = [
    "bess_energy_next",
    "physical_flow",
    "delivered_import_mwh",
    "settled_export_mwh",
    "shift_energy_mwh",
    "dr_deadline_is_conserved",
    "rebound_within_contract",
    "SlotCostRates",
    "slot_cost_rates",
]
