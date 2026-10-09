"""Versioned, provenance-labelled synthetic model assumptions."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .canonical import sha256_hex
from .config import FIXTURE_ROOT, MARKET_AUCTION_SESSION_SLOTS, MARKET_SUBMISSION_LAG_SLOTS
from .contracts import Provenance, RecordModel, StrictModel, _id


class CarbonFactorAssumption(RecordModel):
    factor_version: str
    carbon_kgco2e_per_mwh: float = Field(gt=0)
    provenance: Provenance

    @model_validator(mode="after")
    def verify_fixture_lineage(self):
        expected = sha256_hex({
            "factor_version": self.factor_version,
            "carbon_kgco2e_per_mwh": self.carbon_kgco2e_per_mwh,
        })
        if self.provenance.content_sha256 != expected:
            raise ValueError("carbon factor provenance digest does not match assumption payload")
        return self


def load_carbon_factor(path: Path | None = None) -> CarbonFactorAssumption:
    source = path or (FIXTURE_ROOT / "carbon_factor.json")
    return CarbonFactorAssumption.model_validate_json(source.read_text(encoding="utf-8"))


class FlexibilityCostEntry(StrictModel):
    load_id: str
    shed_cost_usd_per_mwh: float = Field(ge=0)
    shift_cost_usd_per_mwh: float = Field(ge=0)

    _load_id = field_validator("load_id")(_id)


class FlexibilityCostAssumption(RecordModel):
    flexibility_cost_version: str
    currency: Literal["USD"]
    entries: list[FlexibilityCostEntry]
    source_note: str
    provenance: Provenance

    @model_validator(mode="after")
    def verify_fixture_lineage(self):
        if len({entry.load_id for entry in self.entries}) != len(self.entries):
            raise ValueError("flexibility cost load ids must be unique")
        if [entry.load_id for entry in self.entries] != sorted(entry.load_id for entry in self.entries):
            raise ValueError("flexibility cost entries must be sorted by load_id")
        expected = sha256_hex({
            "flexibility_cost_version": self.flexibility_cost_version,
            "currency": self.currency,
            "entries": [entry.model_dump(mode="python") for entry in self.entries],
            "source_note": self.source_note,
        })
        if self.provenance.content_sha256 != expected:
            raise ValueError("flexibility cost provenance digest does not match assumption payload")
        return self


def load_flexibility_costs(path: Path | None = None) -> FlexibilityCostAssumption:
    source = path or (FIXTURE_ROOT / "flexibility_costs.json")
    return FlexibilityCostAssumption.model_validate_json(source.read_text(encoding="utf-8"))


class MarketSettlementAssumption(RecordModel):
    settlement_version: str
    currency: Literal["USD"]
    submission_lag_slots: int = Field(ge=0)
    auction_session_slots: int = Field(ge=0)
    nonmarket_export_label: str
    nonmarket_export_rate_usd_per_mwh: float = Field(ge=0)
    source_note: str
    provenance: Provenance

    @model_validator(mode="after")
    def verify_fixture_lineage(self):
        if self.submission_lag_slots != MARKET_SUBMISSION_LAG_SLOTS:
            raise ValueError("submission_lag_slots must equal MARKET_SUBMISSION_LAG_SLOTS")
        if self.auction_session_slots != MARKET_AUCTION_SESSION_SLOTS:
            raise ValueError("auction_session_slots must equal MARKET_AUCTION_SESSION_SLOTS")
        expected = sha256_hex({
            "settlement_version": self.settlement_version,
            "currency": self.currency,
            "submission_lag_slots": self.submission_lag_slots,
            "auction_session_slots": self.auction_session_slots,
            "nonmarket_export_label": self.nonmarket_export_label,
            "nonmarket_export_rate_usd_per_mwh": self.nonmarket_export_rate_usd_per_mwh,
            "source_note": self.source_note,
        })
        if self.provenance.content_sha256 != expected:
            raise ValueError("market settlement provenance digest does not match assumption payload")
        return self


def load_market_settlement(path: Path | None = None) -> MarketSettlementAssumption:
    source = path or (FIXTURE_ROOT / "market_settlement.json")
    return MarketSettlementAssumption.model_validate_json(source.read_text(encoding="utf-8"))


class DsmSlab(StrictModel):
    lower_fraction: float = Field(ge=0)
    upper_fraction: float | None = Field(default=None)
    rate_multiplier: float = Field(ge=0)


class DsmTechnologyBands(StrictModel):
    tolerance_band_fraction: float = Field(ge=0, le=1)
    slabs: list[DsmSlab]

    @model_validator(mode="after")
    def check_slabs(self):
        if not self.slabs:
            raise ValueError("technology must declare at least one slab")
        if abs(self.slabs[0].lower_fraction - self.tolerance_band_fraction) > 1e-12:
            raise ValueError("first slab lower must equal tolerance_band_fraction")
        for current, nxt in zip(self.slabs, self.slabs[1:]):
            if current.upper_fraction is None or abs(current.upper_fraction - nxt.lower_fraction) > 1e-12:
                raise ValueError("slabs must be contiguous")
            if nxt.rate_multiplier <= current.rate_multiplier:
                raise ValueError("rate multipliers must be strictly increasing")
        if self.slabs[-1].upper_fraction is not None:
            raise ValueError("terminal slab must be unbounded (upper_fraction null)")
        return self


class DsmRegime(StrictModel):
    regime_id: str
    effective_from: dt.date
    effective_until: dt.date | None = None
    technologies: dict[str, DsmTechnologyBands]

    @model_validator(mode="after")
    def check_regime(self):
        if self.effective_until is not None and not (self.effective_from < self.effective_until):
            raise ValueError("effective_from must precede effective_until")
        if set(self.technologies) != {"solar", "wind"}:
            raise ValueError("regime technologies must be exactly solar and wind")
        return self


class DsmRegimeSet(RecordModel):
    regime_version: str
    currency: Literal["USD"]
    regimes: list[DsmRegime]
    source_note: str
    provenance: Provenance

    @model_validator(mode="after")
    def verify_fixture_lineage(self):
        ordered = sorted(self.regimes, key=lambda r: r.effective_from)
        if [r.regime_id for r in ordered] != [r.regime_id for r in self.regimes]:
            raise ValueError("regimes must be sorted by effective_from")
        for current, nxt in zip(ordered, ordered[1:]):
            if current.effective_until is None or current.effective_until >= nxt.effective_from:
                raise ValueError("regimes must be non-overlapping and dated")
        expected = sha256_hex({
            "regime_version": self.regime_version,
            "currency": self.currency,
            "regimes": [r.model_dump(mode="python") for r in self.regimes],
            "source_note": self.source_note,
        })
        if self.provenance.content_sha256 != expected:
            raise ValueError("dsm regime provenance digest does not match assumption payload")
        return self

    def regime_for(self, effective_date: dt.date) -> DsmRegime | None:
        for regime in self.regimes:
            if regime.effective_from <= effective_date and (regime.effective_until is None or effective_date <= regime.effective_until):
                return regime
        return None


def load_dsm_regimes(path: Path | None = None) -> DsmRegimeSet:
    source = path or (FIXTURE_ROOT / "dsm_regimes.json")
    return DsmRegimeSet.model_validate_json(source.read_text(encoding="utf-8"))


class ObligationHeuristicAssumption(RecordModel):
    heuristic_version: str
    comfort_margin_fraction: float = Field(ge=0)
    risk_floor_fraction: float = Field(ge=0)
    urgency_exponent: float = Field(gt=0)
    price_cap_multiplier: float = Field(ge=0)
    source_note: str
    provenance: Provenance

    @model_validator(mode="after")
    def verify_fixture_lineage(self):
        expected = sha256_hex({
            "heuristic_version": self.heuristic_version,
            "comfort_margin_fraction": self.comfort_margin_fraction,
            "risk_floor_fraction": self.risk_floor_fraction,
            "urgency_exponent": self.urgency_exponent,
            "price_cap_multiplier": self.price_cap_multiplier,
            "source_note": self.source_note,
        })
        if self.provenance.content_sha256 != expected:
            raise ValueError("obligation heuristic provenance digest does not match assumption payload")
        return self


def load_obligation_heuristic(path: Path | None = None) -> ObligationHeuristicAssumption:
    source = path or (FIXTURE_ROOT / "obligation_heuristic.json")
    return ObligationHeuristicAssumption.model_validate_json(source.read_text(encoding="utf-8"))
