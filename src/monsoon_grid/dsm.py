"""Deviation Settlement Mechanism charge calculator (report-only).

Computed post-hoc from realized vs. scheduled MW; nothing in the planner or
fallback consumes DSM values. The slab rate_multipliers are scenario assumptions
under the modelled CERC DSM mechanism, never regulatory fact. See
``fixtures/dsm_regimes.json`` and its source_note.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import Field, model_validator

from .assumptions import DsmRegimeSet, load_dsm_regimes
from .config import DT_HOURS
from .contracts import Provenance, RecordModel
from .errors import DsmInputError


class DsmChargeResult(RecordModel):
    asset_id: str
    slot_index: int = Field(ge=0, le=95)
    technology: Literal["solar", "wind"]
    actual_mw: float
    scheduled_mw: float
    available_capacity_mw: float = Field(gt=0)
    error_fraction: float
    absolute_error_fraction: float = Field(ge=0)
    direction: Literal["over_injection", "under_injection", "within_band"]
    slab_index: int | None = None
    slab_lower_fraction: float | None = None
    slab_upper_fraction: float | None = None
    rate_multiplier: float = Field(ge=0)
    reference_rate_usd_per_mwh: float = Field(ge=0)
    charge_usd: float = Field(ge=0)
    regime_id: str
    regime_version: str
    tolerance_band_fraction: float = Field(ge=0, le=1)
    assumption_class: Literal["computed_from_assumed_rates_under_real_mechanism"]
    provenance: Provenance

    @model_validator(mode="after")
    def _check_direction(self):
        if self.direction == "within_band" and self.charge_usd != 0.0:
            raise ValueError("within_band deviation must carry zero charge")
        return self


def compute_dsm_charge(
    *,
    actual_mw: float,
    scheduled_mw: float,
    available_capacity_mw: float,
    technology: Literal["solar", "wind"],
    reference_rate_usd_per_mwh: float,
    effective_date: dt.date,
    regimes: DsmRegimeSet | None = None,
    asset_id: str = "portfolio",
    slot_index: int = 0,
) -> DsmChargeResult:
    """Compute the DSM charge for one block under the regime effective on ``effective_date``.

    Half-open ``[lower, upper)`` slab convention; tolerance comparison is ``<=``.
    Charge uses ``abs(actual - scheduled)`` so over/under injection of equal
    magnitude cost the same and differ only in ``direction``.
    """
    regimes = regimes or load_dsm_regimes()
    if available_capacity_mw <= 0:
        raise DsmInputError("DSM normalization is undefined for zero available capacity", code="DSM_ZERO_AVAILABLE_CAPACITY")
    regime = regimes.regime_for(effective_date)
    if regime is None:
        raise DsmInputError(f"no DSM regime effective on {effective_date.isoformat()}", code="DSM_NO_REGIME_FOR_DATE")

    bands = regime.technologies[technology]
    error_fraction = (actual_mw - scheduled_mw) / available_capacity_mw
    absolute = abs(error_fraction)

    provenance = Provenance(label="scenario_assumption", source_id="dsm-calculator", source_version="dsm-charge-v1", input_refs=[])

    if absolute <= bands.tolerance_band_fraction:
        return DsmChargeResult(
            asset_id=asset_id,
            slot_index=slot_index,
            technology=technology,
            actual_mw=round(actual_mw, 6),
            scheduled_mw=round(scheduled_mw, 6),
            available_capacity_mw=round(available_capacity_mw, 6),
            error_fraction=round(error_fraction, 6),
            absolute_error_fraction=round(absolute, 6),
            direction="within_band",
            slab_index=None,
            slab_lower_fraction=None,
            slab_upper_fraction=None,
            rate_multiplier=0.0,
            reference_rate_usd_per_mwh=round(reference_rate_usd_per_mwh, 6),
            charge_usd=0.0,
            regime_id=regime.regime_id,
            regime_version=regimes.regime_version,
            tolerance_band_fraction=bands.tolerance_band_fraction,
            assumption_class="computed_from_assumed_rates_under_real_mechanism",
            provenance=provenance,
        )

    selected_index = None
    selected = None
    for index, slab in enumerate(bands.slabs):
        if slab.upper_fraction is None:
            if absolute >= slab.lower_fraction:
                selected_index, selected = index, slab
                break
        elif slab.lower_fraction <= absolute < slab.upper_fraction:
            selected_index, selected = index, slab
            break
    if selected is None:
        # absolute is at or beyond the terminal lower edge handled above; otherwise
        # it falls below the first slab, which the tolerance check already covered.
        selected_index, selected = len(bands.slabs) - 1, bands.slabs[-1]

    charge = round(abs(actual_mw - scheduled_mw) * DT_HOURS * reference_rate_usd_per_mwh * selected.rate_multiplier, 6)
    return DsmChargeResult(
        asset_id=asset_id,
        slot_index=slot_index,
        technology=technology,
        actual_mw=round(actual_mw, 6),
        scheduled_mw=round(scheduled_mw, 6),
        available_capacity_mw=round(available_capacity_mw, 6),
        error_fraction=round(error_fraction, 6),
        absolute_error_fraction=round(absolute, 6),
        direction="over_injection" if actual_mw > scheduled_mw else "under_injection",
        slab_index=selected_index,
        slab_lower_fraction=selected.lower_fraction,
        slab_upper_fraction=selected.upper_fraction,
        rate_multiplier=selected.rate_multiplier,
        reference_rate_usd_per_mwh=round(reference_rate_usd_per_mwh, 6),
        charge_usd=charge,
        regime_id=regime.regime_id,
        regime_version=regimes.regime_version,
        tolerance_band_fraction=bands.tolerance_band_fraction,
        assumption_class="computed_from_assumed_rates_under_real_mechanism",
        provenance=provenance,
    )
