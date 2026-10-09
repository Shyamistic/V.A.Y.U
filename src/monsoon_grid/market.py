"""Market commitment validation, submission/delivery lag, and settlement arithmetic.

This module separates market position from physical flow (design A4).  It is the
sole owner of the ``MARKET_GATE_CLOSED`` error code.  Nothing here submits to a
real market: ``submit_commitment`` only validates the temporal legality of a
submission against the auction session and returns a cleared commitment.
"""

from __future__ import annotations

from typing import Sequence

from pydantic import Field

from .config import DT_HOURS, MARKET_SUBMISSION_LAG_SLOTS
from .contracts import (
    MarketCommitment,
    MarketIntent,
    StrictModel,
    market_submission_open,
)
from .errors import MarketGateClosedError

__all__ = [
    "MARKET_SUBMISSION_LAG_SLOTS",
    "SlotSettlement",
    "intent_from_commitment",
    "submit_commitment",
    "committed_export_mw",
    "settle_slot",
]


class SlotSettlement(StrictModel):
    committed_export_mw: float = Field(ge=0)
    uncommitted_export_mw: float = Field(ge=0)
    market_revenue_usd: float = Field(ge=0)
    nonmarket_export_valuation_usd: float = Field(ge=0)
    market_shortfall_mwh: float = Field(ge=0)
    market_shortfall_penalty_usd: float = Field(ge=0)


def intent_from_commitment(commitment: MarketCommitment) -> MarketIntent:
    """Derive the per-slot delivery predicate from the authoritative commitment."""
    cleared = commitment.clearing_status == "CLEARED"
    return MarketIntent(
        market_id=commitment.market_id,
        submission_slot=commitment.submission_slot,
        delivery_start_slot=commitment.delivery_start_slot,
        delivery_end_slot=commitment.delivery_end_slot,
        quantity_mwh=commitment.quantity_mwh,
        price_usd_per_mwh=commitment.price_usd_per_mwh,
        gate_open_slot=commitment.gate_open_slot,
        gate_close_slot=commitment.gate_close_slot,
        approval_state="APPROVED" if cleared else "PENDING",
        settlement_state="OPEN" if cleared else "CLOSED",
        fee_usd_per_mwh=commitment.fee_usd_per_mwh,
        loss_fraction=commitment.loss_fraction,
    )


def submit_commitment(commitment: MarketCommitment, *, replay_slot: int) -> MarketCommitment:
    """Validate the act of submitting at ``replay_slot`` and return a cleared commitment.

    Raises ``MarketGateClosedError`` (code ``MARKET_GATE_CLOSED``) when the
    submission falls at or outside the auction session.
    """
    intent = intent_from_commitment(commitment)
    if not market_submission_open(intent, replay_slot=replay_slot):
        raise MarketGateClosedError(
            f"submission at slot {replay_slot} is outside the auction session "
            f"[{commitment.gate_open_slot}, {commitment.gate_close_slot})"
        )
    return commitment.model_copy(update={"clearing_status": "CLEARED"})


def committed_export_mw(commitments: Sequence[MarketCommitment], *, slot_index: int) -> float:
    """Total committed export MW due for delivery at ``slot_index``."""
    from .contracts import market_delivery_due

    total = 0.0
    for commitment in commitments:
        intent = intent_from_commitment(commitment)
        if market_delivery_due(intent, delivery_slot=slot_index):
            total += commitment.quantity_mwh / ((commitment.delivery_end_slot - commitment.delivery_start_slot + 1) * DT_HOURS)
    return round(total, 6)


def settle_slot(
    commitments: Sequence[MarketCommitment],
    *,
    slot_index: int,
    export_mw: float,
    nonmarket_export_rate_usd_per_mwh: float,
    grid_loss_fraction: float,
) -> SlotSettlement:
    """Split physical export into committed/uncommitted and value each stream.

    Committed MWh are delivered committed export net of commitment fee and loss and
    priced at the commitment rate; a shortfall against the committed quantity is
    penalised; uncommitted export is valued at the labelled non-market rate.
    """
    from .contracts import market_delivery_due

    due_mw = committed_export_mw(commitments, slot_index=slot_index)
    committed = min(max(0.0, export_mw), due_mw)
    uncommitted = max(0.0, export_mw) - committed
    shortfall_mw = max(0.0, due_mw - committed)

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

    market_revenue = max(0.0, committed * DT_HOURS * committed_rate)
    nonmarket_valuation = uncommitted * DT_HOURS * nonmarket_export_rate_usd_per_mwh * (1.0 - grid_loss_fraction)
    shortfall_mwh = shortfall_mw * DT_HOURS
    shortfall_penalty = shortfall_mwh * shortfall_rate
    return SlotSettlement(
        committed_export_mw=round(committed, 6),
        uncommitted_export_mw=round(uncommitted, 6),
        market_revenue_usd=round(market_revenue, 6),
        nonmarket_export_valuation_usd=round(max(0.0, nonmarket_valuation), 6),
        market_shortfall_mwh=round(shortfall_mwh, 6),
        market_shortfall_penalty_usd=round(max(0.0, shortfall_penalty), 6),
    )
