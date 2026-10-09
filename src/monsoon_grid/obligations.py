"""PPA delivery-obligation attainment tracking and a shadow-price heuristic.

Report-only (design D2). Nothing in the planner or fallback imports this module.
The shadow price is a heuristic placeholder; the principled version is a dual
variable from a multistage stochastic programme. Parameters come from
``fixtures/obligation_heuristic.json`` (never code literals).

Delivered energy for a peak-window obligation is portfolio net output over the
peak slots: ``(renewable_dispatch_mw + sum(bess_discharge_mw) - sum(bess_charge_mw)) * DT``.
"""

from __future__ import annotations

from typing import Sequence

from .assumptions import ObligationHeuristicAssumption, load_obligation_heuristic
from .config import DT_HOURS, SLOT_MINUTES
from .contracts import DeliveryObligation, ObligationAttainment, PortfolioFixture, Provenance


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class ObligationTracker:
    def __init__(self, obligations: Sequence[DeliveryObligation], *, portfolio: PortfolioFixture, heuristic: ObligationHeuristicAssumption | None = None) -> None:
        self.obligations = list(obligations)
        self.portfolio = portfolio
        self.heuristic = heuristic or load_obligation_heuristic()
        self._delivered: dict[str, float] = {o.obligation_id: 0.0 for o in self.obligations}

    def _period_slots(self, obligation: DeliveryObligation) -> int:
        if obligation.obligation_type == "peak_window_energy":
            return len(obligation.peak_window_slots)
        seconds = (obligation.measurement_period_end_utc - obligation.measurement_period_start_utc).total_seconds()
        return max(1, int(seconds / (SLOT_MINUTES * 60)))

    def _committed_mwh(self, obligation: DeliveryObligation, period_slots: int) -> float:
        if obligation.obligation_type == "peak_window_energy":
            return obligation.committed_value
        return obligation.committed_value * (obligation.reference_capacity_mw or 0.0) * period_slots * DT_HOURS

    def update(self, slot_index: int, delivered_mwh_by_slot: Sequence[float]) -> list[ObligationAttainment]:
        attainments: list[ObligationAttainment] = []
        h = self.heuristic
        for obligation in self.obligations:
            period_slots = self._period_slots(obligation)
            committed_mwh = self._committed_mwh(obligation, period_slots)
            effective_commitment = committed_mwh * (1.0 - obligation.relaxation_allowance_fraction)

            if obligation.obligation_type == "peak_window_energy":
                peak_set = set(obligation.peak_window_slots)
                delivered = sum(delivered_mwh_by_slot[s] for s in range(min(slot_index + 1, len(delivered_mwh_by_slot))) if s in peak_set)
                slots_elapsed = sum(1 for s in obligation.peak_window_slots if s <= slot_index)
            else:
                delivered = sum(delivered_mwh_by_slot[: slot_index + 1])
                slots_elapsed = min(slot_index + 1, period_slots)

            self._delivered[obligation.obligation_id] = delivered
            elapsed_fraction = slots_elapsed / period_slots if period_slots else 0.0
            attainment_to_date = delivered / effective_commitment if effective_commitment > 0 else 0.0

            if elapsed_fraction > 0:
                projected = _clamp(attainment_to_date / max(elapsed_fraction, 1.0 / period_slots), 0.0, 10.0)
            else:
                projected = None

            if projected is None:
                urgency = 0.0
            else:
                denom = (1.0 + h.comfort_margin_fraction - h.risk_floor_fraction)
                raw = (1.0 + h.comfort_margin_fraction - projected) / denom if denom else 0.0
                urgency = _clamp(raw, 0.0, 1.0) ** h.urgency_exponent

            price = round(min(urgency * obligation.shortfall_penalty_usd_per_mwh * h.price_cap_multiplier, obligation.shortfall_penalty_usd_per_mwh * h.price_cap_multiplier), 6)
            remaining = max(0.0, effective_commitment - delivered)

            attainments.append(ObligationAttainment(
                obligation_id=obligation.obligation_id,
                slot_index=slot_index,
                effective_commitment_mwh=round(effective_commitment, 6),
                delivered_mwh_to_date=round(delivered, 6),
                elapsed_fraction_of_period=round(elapsed_fraction, 6),
                attainment_to_date_fraction=round(attainment_to_date, 6),
                projected_attainment_fraction=None if projected is None else round(projected, 6),
                remaining_energy_required_mwh=round(remaining, 6),
                urgency=round(urgency, 6),
                obligation_shadow_price_usd_per_mwh=price,
                provenance=Provenance(label="derived", source_id="obligation-tracker", source_version="obligation-shadow-price-heuristic-v1", input_refs=[obligation.obligation_id]),
            ))
        return attainments

    @staticmethod
    def portfolio_shadow_price(attainments: Sequence[ObligationAttainment]) -> float:
        return round(max((a.obligation_shadow_price_usd_per_mwh for a in attainments), default=0.0), 6)
