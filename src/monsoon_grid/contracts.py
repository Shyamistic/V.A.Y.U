"""Normative Pydantic v2 contracts for every MonsoonGrid boundary.

The models are intentionally closed (`extra=forbid`) and strict. Numeric replay
records use explicit units and finite values; authoritative state never crosses a
module boundary as an untyped dictionary.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from copy import deepcopy
from typing import Annotated, Any, Generic, Literal, Mapping, TypeVar, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from .canonical import sha256_hex
from .config import (
    BESS_NUMERIC_TOLERANCE_MWH,
    DT_HOURS,
    MARKET_AUCTION_SESSION_SLOTS,
    MARKET_SUBMISSION_LAG_SLOTS,
    REPLAY_BUDGET_SECONDS,
    SCHEMA_VERSION,
    SLOT_COUNT,
)

Unit = Literal["MW", "MWh", "fraction", "USD", "USD_per_MWh", "kgCO2e", "kgCO2e_per_MWh", "count", "seconds"]
SignalName = Literal["power_mw", "energy_mwh", "soc_fraction", "reserve_mwh", "price_usd_per_mwh", "carbon_kgco2e"]
QualityFlag = Literal["fresh", "stale", "missing", "conflict", "invalid"]
ProvenanceLabel = Literal["measured", "derived", "synthetic", "scenario_assumption"]
PolicyMode = Literal["reliability_first", "balanced", "cost_first"]
ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
RESERVED_ACTOR_IDS = frozenset({"batch-replay-synthetic"})


def _id(value: str) -> str:
    if value is None:
        return value
    if not ID_RE.fullmatch(value):
        raise ValueError("identifier must match [A-Za-z0-9_.:-]+")
    return value


def _hash(value: str) -> str:
    if value is None:
        return value
    if not HASH_RE.fullmatch(value):
        raise ValueError("value must be a lowercase SHA-256 hex digest")
    return value


def _utc(value: dt.datetime) -> dt.datetime:
    if value is None:
        return value
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware UTC")
    return value.astimezone(dt.timezone.utc)


def _finite(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("numeric values must be finite")
    if abs(value) >= 0.000001 and round(value, 6) != value:
        raise ValueError("numeric values may have at most six decimal places")
    return value


def _unique_ids(values: list[str]) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError("IDs must be unique")
    return values


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, strict=True, validate_default=True)

    @model_validator(mode="after")
    def _finite_values(self):
        def check(value: Any) -> None:
            if isinstance(value, float):
                _finite(value)
            elif isinstance(value, list):
                for item in value:
                    check(item)
            elif isinstance(value, dict):
                for item in value.values():
                    check(item)

        check(self.model_dump(mode="python"))
        return self


class RecordModel(StrictModel):
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    record_id: str | None = None

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False):
        """Validate deterministic updates and recompute the record identifier.

        Pydantic's default ``model_copy(update=...)`` intentionally skips
        validation.  That is unsafe for replay records because a deterministic
        update can otherwise leave a stale ``record_id`` attached to changed
        portfolio, event, path, or proposal content.
        """
        values = self.model_dump(mode="python", exclude_none=False)
        if deep:
            values = deepcopy(values)
        updates = dict(update or {})
        values.update(updates)
        if "record_id" not in updates:
            values["record_id"] = None
        return type(self).model_validate(values)

    @model_validator(mode="after")
    def assign_record_id(self):
        if self.record_id is None:
            object.__setattr__(self, "record_id", sha256_hex(self.model_dump(mode="python", exclude={"record_id"})))
        return self


class Provenance(StrictModel):
    label: ProvenanceLabel
    source_id: str
    source_version: str
    content_sha256: str | None = None
    transform_version: str | None = None
    input_refs: list[str] = Field(default_factory=list)

    _source_id = field_validator("source_id")(_id)
    _hash = field_validator("content_sha256")(_hash)

    @model_validator(mode="after")
    def validate_lineage(self):
        if self.label == "derived" and not self.input_refs:
            raise ValueError("derived provenance requires input_refs")
        if self.label == "synthetic" and not self.content_sha256:
            raise ValueError("synthetic provenance requires content_sha256")
        return self


class OutageState(StrictModel):
    status: Literal["available", "unavailable"] = "available"
    reason: str | None = None
    start_slot: int | None = Field(default=None, ge=0, le=95)
    end_slot: int | None = Field(default=None, ge=0, le=95)

    @model_validator(mode="after")
    def valid_interval(self):
        if self.status == "unavailable" and not self.reason:
            raise ValueError("unavailable assets require a reason")
        if self.start_slot is not None and self.end_slot is not None and self.end_slot < self.start_slot:
            raise ValueError("outage end_slot must not precede start_slot")
        return self


class MaintenanceState(StrictModel):
    status: Literal["none", "scheduled", "active", "complete"] = "none"
    start_slot: int | None = Field(default=None, ge=0, le=95)
    end_slot: int | None = Field(default=None, ge=0, le=95)
    cannot_delay: bool = False

    @model_validator(mode="after")
    def valid_interval(self):
        if self.start_slot is not None and self.end_slot is not None and self.end_slot < self.start_slot:
            raise ValueError("maintenance end_slot must not precede start_slot")
        return self


class AssetBase(RecordModel):
    asset_id: str
    display_name: str
    node_id: str
    region: str
    enabled: bool = True
    outage: OutageState = Field(default_factory=OutageState)
    maintenance: MaintenanceState = Field(default_factory=MaintenanceState)
    provenance: Provenance

    _asset_id = field_validator("asset_id", "node_id")(_id)


class SolarFarmSpec(AssetBase):
    kind: Literal["solar_farm"] = "solar_farm"
    capacity_mw: float = Field(gt=0)
    ramp_mw_per_slot: float = Field(gt=0)
    availability_fraction: float = Field(ge=0, le=1)
    profile_id: str


class WindFarmSpec(AssetBase):
    kind: Literal["wind_farm"] = "wind_farm"
    capacity_mw: float = Field(gt=0)
    ramp_mw_per_slot: float = Field(gt=0)
    availability_fraction: float = Field(ge=0, le=1)
    profile_id: str


class BessSpec(AssetBase):
    kind: Literal["bess"] = "bess"
    capacity_mwh: float = Field(gt=0)
    charge_rate_mw: float = Field(gt=0)
    discharge_rate_mw: float = Field(gt=0)
    eta_c: float = Field(gt=0, le=1)
    eta_d: float = Field(gt=0, le=1)
    initial_energy_mwh: float = Field(ge=0)
    reserve_mwh: float = Field(ge=0)
    terminal_target_mwh: float = Field(ge=0)

    @model_validator(mode="after")
    def bounds(self):
        if self.initial_energy_mwh > self.capacity_mwh:
            raise ValueError("initial_energy_mwh cannot exceed capacity_mwh")
        if self.reserve_mwh > self.initial_energy_mwh:
            raise ValueError("reserve_mwh cannot exceed initial_energy_mwh")
        if not self.reserve_mwh <= self.terminal_target_mwh <= self.capacity_mwh:
            raise ValueError("terminal_target_mwh must be between reserve and capacity")
        return self


class LoadClassSpec(AssetBase):
    kind: Literal["load_class"] = "load_class"
    critical: bool
    base_profile_id: str
    max_shed_mw: float = Field(ge=0)
    max_shift_mw: float = Field(ge=0)
    shift_deadline_slot: int = Field(ge=0, le=95)
    rebound_rate_mw: float = Field(ge=0)
    rebound_window_slots: list[int] = Field(default_factory=list)
    contracted_rebound_mwh: float = Field(default=0, ge=0)
    shed_cost_usd_per_mwh: float = Field(ge=0)
    shift_cost_usd_per_mwh: float = Field(ge=0)
    # F3: upper bound for the UNPRICED voluntary non-critical reduction lever.
    # Defaults to 0 (lever off). Reliability-relief capacity on the load class,
    # NOT a contracted market product (so the DR-contract model omits it).
    max_reduce_mw: float = Field(default=0, ge=0)
    opt_in: bool = True
    opt_out: bool = False

    @model_validator(mode="after")
    def load_rules(self):
        if self.critical and any((self.max_shed_mw, self.max_shift_mw, self.rebound_rate_mw, self.contracted_rebound_mwh)):
            raise ValueError("critical loads cannot have DR capacity")
        if self.critical and (self.shed_cost_usd_per_mwh or self.shift_cost_usd_per_mwh):
            raise ValueError("critical loads must carry zero flexibility cost")
        if self.critical and self.max_reduce_mw:
            raise ValueError("critical loads cannot declare non-critical reduction capacity")
        if self.opt_out and (self.max_shed_mw or self.max_shift_mw or self.rebound_rate_mw):
            raise ValueError("opt-out loads cannot declare active DR capacity")
        if self.opt_out and self.max_reduce_mw:
            raise ValueError("opt-out loads cannot declare non-critical reduction capacity")
        if any(slot < 0 or slot >= SLOT_COUNT for slot in self.rebound_window_slots):
            raise ValueError("rebound window slot outside replay")
        return self


class GridInterfaceSpec(AssetBase):
    kind: Literal["grid_interface"] = "grid_interface"
    import_limit_mw: float = Field(ge=0)
    export_limit_mw: float = Field(ge=0)
    ramp_mw_per_slot: float = Field(ge=0)
    loss_fraction: float = Field(ge=0, lt=1)
    fee_usd_per_mwh: float = Field(ge=0)


class TieLineSpec(AssetBase):
    kind: Literal["tie_line"] = "tie_line"
    capacity_mw: float = Field(gt=0)
    ramp_mw_per_slot: float = Field(gt=0)
    derate_fraction: float = Field(ge=0, le=1)
    initial_flow_mw: float = 0.0


class BenchEndpointSpec(AssetBase):
    kind: Literal["bench_endpoint"] = "bench_endpoint"
    endpoint_id: str
    protocol_version: str

    _endpoint = field_validator("endpoint_id")(_id)


AssetSpec = Annotated[
    Union[SolarFarmSpec, WindFarmSpec, BessSpec, LoadClassSpec, GridInterfaceSpec, TieLineSpec, BenchEndpointSpec],
    Field(discriminator="kind"),
]
AssetAdapter = TypeAdapter(AssetSpec)


class DRContract(RecordModel):
    load_id: str
    opt_in: bool
    opt_out: bool = False
    critical: bool
    max_shed_mw: float = Field(ge=0)
    max_shift_mw: float = Field(ge=0)
    shift_deadline_slot: int = Field(ge=0, le=95)
    rebound_rate_mw: float = Field(ge=0)
    rebound_window_slots: list[int]
    contracted_rebound_mwh: float = Field(ge=0)

    _load_id = field_validator("load_id")(_id)

    @model_validator(mode="after")
    def check_contract(self):
        if self.critical and (self.max_shed_mw or self.max_shift_mw or self.rebound_rate_mw):
            raise ValueError("critical DR contract must be zero")
        if self.opt_out and (self.max_shed_mw or self.max_shift_mw):
            raise ValueError("opt-out DR contract must be zero")
        return self


class MarketIntent(RecordModel):
    market_id: str
    submission_slot: int = Field(ge=0, le=95)
    delivery_start_slot: int = Field(ge=0, le=95)
    delivery_end_slot: int = Field(ge=0, le=95)
    quantity_mwh: float = Field(ge=0, le=80)
    price_usd_per_mwh: float = Field(ge=0, le=250)
    gate_open_slot: int = Field(ge=0, le=95)
    gate_close_slot: int = Field(ge=0, le=95)
    approval_state: Literal["PENDING", "APPROVED", "REJECTED"] = "PENDING"
    settlement_state: Literal["OPEN", "CLOSED", "SETTLED"] = "OPEN"
    fee_usd_per_mwh: float = Field(ge=0)
    loss_fraction: float = Field(ge=0, lt=1)

    _market_id = field_validator("market_id")(_id)

    @model_validator(mode="after")
    def check_intervals(self):
        _check_market_window(self)
        if self.approval_state == "APPROVED" and self.quantity_mwh <= 0:
            raise ValueError("approved market intent must have positive quantity")
        return self


def _check_market_window(model: Any) -> None:
    """Enforce the six auction-aligned lag/alignment rules (design A4).

    Shared by MarketIntent and MarketCommitment so a hand-written intent cannot
    bypass what a commitment enforces.
    """
    if model.gate_open_slot % 2 != 0:
        raise ValueError("gate_open_slot must be half-hour aligned (even)")
    if model.gate_close_slot != model.gate_open_slot + MARKET_AUCTION_SESSION_SLOTS:
        raise ValueError("auction session length must be MARKET_AUCTION_SESSION_SLOTS")
    if not model.gate_open_slot <= model.submission_slot < model.gate_close_slot:
        raise ValueError("submission_slot must be inside the auction session")
    if model.delivery_start_slot < model.submission_slot + MARKET_SUBMISSION_LAG_SLOTS:
        raise ValueError("delivery must lag submission by MARKET_SUBMISSION_LAG_SLOTS")
    if model.delivery_end_slot < model.delivery_start_slot:
        raise ValueError("delivery interval is reversed")
    if model.delivery_end_slot - model.delivery_start_slot + 1 > 4:
        raise ValueError("delivery block must be at most one hour (4 slots)")


def market_submission_open(intent: MarketIntent, *, replay_slot: int) -> bool:
    """True while the auction session accepting this submission is open."""
    return intent.gate_open_slot <= replay_slot < intent.gate_close_slot


def market_delivery_due(intent: MarketIntent, *, delivery_slot: int) -> bool:
    """True when this slot lies in the committed, approved, open delivery window."""
    return (
        intent.delivery_start_slot <= delivery_slot <= intent.delivery_end_slot
        and intent.approval_state == "APPROVED"
        and intent.settlement_state == "OPEN"
        and intent.quantity_mwh > 0
    )


class MarketCommitment(RecordModel):
    commitment_id: str
    market_id: str
    submission_slot: int = Field(ge=0, le=95)
    delivery_start_slot: int = Field(ge=0, le=95)
    delivery_end_slot: int = Field(ge=0, le=95)
    quantity_mwh: float = Field(ge=0)
    price_usd_per_mwh: float = Field(ge=0)
    fee_usd_per_mwh: float = Field(ge=0)
    loss_fraction: float = Field(ge=0, lt=1)
    shortfall_penalty_usd_per_mwh: float = Field(ge=0)
    gate_open_slot: int = Field(ge=0, le=95)
    gate_close_slot: int = Field(ge=0, le=95)
    clearing_status: Literal["SUBMITTED", "CLEARED", "UNCLEARED", "CANCELLED"]
    provenance: Provenance

    _ids = field_validator("commitment_id", "market_id")(_id)

    @model_validator(mode="after")
    def check_commitment(self):
        _check_market_window(self)
        if self.clearing_status == "CLEARED" and self.quantity_mwh <= 0:
            raise ValueError("cleared commitment must have positive quantity")
        if self.clearing_status in {"UNCLEARED", "CANCELLED"} and self.quantity_mwh != 0:
            raise ValueError("uncleared or cancelled commitment must have zero quantity")
        return self


class DeliveryObligation(RecordModel):
    obligation_id: str
    obligation_type: Literal["annual_cuf", "peak_window_energy", "rtc_availability"]
    committed_value: float = Field(gt=0)
    committed_unit: Unit
    measurement_period_start_utc: dt.datetime
    measurement_period_end_utc: dt.datetime
    peak_window_slots: list[int] = Field(default_factory=list)
    relaxation_allowance_fraction: float = Field(ge=0, le=1)
    shortfall_penalty_usd_per_mwh: float = Field(ge=0)
    reference_capacity_mw: float | None = None
    provenance: Provenance

    _ids = field_validator("obligation_id")(_id)
    _period = field_validator("measurement_period_start_utc", "measurement_period_end_utc")(_utc)

    @model_validator(mode="after")
    def check_obligation(self):
        if self.measurement_period_end_utc <= self.measurement_period_start_utc:
            raise ValueError("measurement period end must follow start")
        if any(slot < 0 or slot >= SLOT_COUNT for slot in self.peak_window_slots):
            raise ValueError("peak window slot outside replay")
        if self.peak_window_slots != sorted(set(self.peak_window_slots)):
            raise ValueError("peak window slots must be sorted and unique")
        if self.reference_capacity_mw is not None and self.reference_capacity_mw <= 0:
            raise ValueError("reference_capacity_mw must be positive when present")
        if self.obligation_type == "peak_window_energy":
            if not self.peak_window_slots:
                raise ValueError("peak_window_energy requires peak_window_slots")
            if self.committed_unit != "MWh":
                raise ValueError("peak_window_energy committed_unit must be MWh")
            if self.reference_capacity_mw is not None:
                raise ValueError("peak_window_energy must not set reference_capacity_mw")
        else:
            if self.committed_unit != "fraction":
                raise ValueError("annual_cuf/rtc_availability committed_unit must be fraction")
            if not (0 < self.committed_value <= 1):
                raise ValueError("fractional obligation committed_value must be in (0, 1]")
            if self.reference_capacity_mw is None:
                raise ValueError("annual_cuf/rtc_availability requires reference_capacity_mw")
            if self.peak_window_slots:
                raise ValueError("annual_cuf/rtc_availability must not set peak_window_slots")
        return self


class ObligationAttainment(RecordModel):
    obligation_id: str
    slot_index: int = Field(ge=0, le=95)
    effective_commitment_mwh: float = Field(ge=0)
    delivered_mwh_to_date: float = Field(ge=0)
    elapsed_fraction_of_period: float = Field(ge=0)
    attainment_to_date_fraction: float = Field(ge=0)
    projected_attainment_fraction: float | None = None
    remaining_energy_required_mwh: float = Field(ge=0)
    urgency: float = Field(ge=0, le=1)
    obligation_shadow_price_usd_per_mwh: float = Field(ge=0)
    provenance: Provenance

    _ids = field_validator("obligation_id")(_id)


class AvailableCapacityRecord(RecordModel):
    asset_id: str
    slot_index: int = Field(ge=0, le=95)
    technology: Literal["solar", "wind", "bess", "other"]
    nameplate_capacity_mw: float = Field(gt=0)
    available_capacity_mw: float = Field(ge=0)
    derate_fraction: float = Field(ge=0, le=1)
    outage_reason: str | None = None
    derate_reason: str | None = None
    provenance: Provenance

    _ids = field_validator("asset_id")(_id)

    @model_validator(mode="after")
    def check_capacity(self):
        if self.available_capacity_mw > self.nameplate_capacity_mw + 1e-6:
            raise ValueError("available capacity cannot exceed nameplate")
        if self.derate_fraction > 0 and not self.derate_reason:
            raise ValueError("a positive derate requires a derate_reason")
        if self.available_capacity_mw == 0 and not self.outage_reason:
            raise ValueError("zero available capacity requires an outage_reason")
        if self.outage_reason is None and abs(self.available_capacity_mw - self.nameplate_capacity_mw * (1 - self.derate_fraction)) > 1e-6:
            raise ValueError("available capacity must equal nameplate * (1 - derate_fraction) absent an outage")
        return self


class ScheduleRecord(RecordModel):
    schedule_id: str
    asset_id: str
    slot_index: int = Field(ge=0, le=95)
    scheduled_mw: float = Field(ge=0)
    submitted_at_utc: dt.datetime
    revision_number: int = Field(ge=0)
    supersedes_revision: int | None = Field(default=None, ge=0)
    sldc_receipt_reference: str | None = None
    provenance: Provenance

    _ids = field_validator("schedule_id", "asset_id")(_id)
    _submitted = field_validator("submitted_at_utc")(_utc)

    @model_validator(mode="after")
    def check_schedule(self):
        if self.supersedes_revision is not None and self.supersedes_revision >= self.revision_number:
            raise ValueError("supersedes_revision must be less than revision_number")
        if self.revision_number > 0 and self.supersedes_revision is None:
            raise ValueError("a revision must declare what it supersedes")
        return self


class PpaTerms(RecordModel):
    ppa_id: str
    counterparty_id: str
    tariff_usd_per_mwh: float = Field(ge=0)
    committed_cuf_fraction: float = Field(ge=0, le=1)
    peak_window_slots: list[int] = Field(default_factory=list)
    shortfall_penalty_usd_per_mwh: float = Field(ge=0)
    excess_payment_fraction: float = Field(ge=0, le=1)
    relaxation_allowance_fraction: float = Field(ge=0, le=1)
    term_start_utc: dt.datetime
    term_end_utc: dt.datetime
    provenance: Provenance

    _ids = field_validator("ppa_id", "counterparty_id")(_id)
    _terms = field_validator("term_start_utc", "term_end_utc")(_utc)

    @model_validator(mode="after")
    def check_ppa(self):
        if self.term_end_utc <= self.term_start_utc:
            raise ValueError("term_end must follow term_start")
        if any(slot < 0 or slot >= SLOT_COUNT for slot in self.peak_window_slots):
            raise ValueError("peak window slot outside replay")
        if self.peak_window_slots != sorted(set(self.peak_window_slots)):
            raise ValueError("peak window slots must be sorted and unique")
        return self


class StorageWarrantyTerms(RecordModel):
    asset_id: str
    cycle_budget_count: int = Field(gt=0)
    throughput_budget_mwh: float = Field(gt=0)
    calendar_life_years: float = Field(gt=0)
    max_depth_of_discharge_fraction: float = Field(gt=0, le=1)
    max_c_rate_charge: float = Field(gt=0)
    max_c_rate_discharge: float = Field(gt=0)
    warranty_start_utc: dt.datetime
    provenance: Provenance

    _ids = field_validator("asset_id")(_id)
    _start = field_validator("warranty_start_utc")(_utc)


class FlexibilityContract(RecordModel):
    contract_id: str
    load_id: str
    shed_cost_usd_per_mwh: float = Field(ge=0)
    shift_cost_usd_per_mwh: float = Field(ge=0)
    notice_period_slots: int = Field(ge=0)
    max_events_per_period: int = Field(ge=0)
    measurement_period_slots: int = Field(gt=0)
    rebound_obligation_mwh: float = Field(ge=0)
    rebound_window_slots: list[int] = Field(default_factory=list)
    provenance: Provenance

    _ids = field_validator("contract_id", "load_id")(_id)

    @model_validator(mode="after")
    def check_flexibility(self):
        if any(slot < 0 or slot >= SLOT_COUNT for slot in self.rebound_window_slots):
            raise ValueError("rebound window slot outside replay")
        if self.rebound_window_slots != sorted(set(self.rebound_window_slots)):
            raise ValueError("rebound window slots must be sorted and unique")
        if self.rebound_obligation_mwh > 0 and not self.rebound_window_slots:
            raise ValueError("a positive rebound obligation requires a rebound window")
        if self.max_events_per_period == 0 and (self.shed_cost_usd_per_mwh or self.shift_cost_usd_per_mwh):
            raise ValueError("an unusable contract (max_events_per_period==0) must carry zero cost")
        return self


class SiteSpec(StrictModel):
    asset_id: str
    display_name: str
    kind: Literal["solar_farm", "wind_farm", "bess"]
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    state: str
    capacity_mw: float | None = None
    capacity_mwh: float | None = None
    commissioning_year: int = Field(ge=2000, le=2035)
    provenance: Provenance

    _ids = field_validator("asset_id")(_id)

    @model_validator(mode="after")
    def check_site(self):
        if self.kind in {"solar_farm", "wind_farm"}:
            if self.capacity_mw is None or self.capacity_mwh is not None:
                raise ValueError("generation sites set capacity_mw and leave capacity_mwh null")
            if self.capacity_mw <= 0:
                raise ValueError("capacity_mw must be positive")
        else:
            if self.capacity_mwh is None or self.capacity_mw is not None:
                raise ValueError("bess sites set capacity_mwh and leave capacity_mw null")
            if self.capacity_mwh <= 0:
                raise ValueError("capacity_mwh must be positive")
        return self


class SitePortfolio(RecordModel):
    site_portfolio_id: str
    fixture_set_version: str
    country: Literal["IN"]
    sites: list[SiteSpec]
    ppa_terms: list[PpaTerms]
    obligations: list[DeliveryObligation]
    flexibility_contracts: list[FlexibilityContract]
    storage_warranties: list[StorageWarrantyTerms]
    dsm_classification: dict[str, Literal["solar", "wind", "bess", "other"]]
    provenance: Provenance

    _ids = field_validator("site_portfolio_id")(_id)

    @model_validator(mode="after")
    def check_site_portfolio(self):
        asset_ids = [site.asset_id for site in self.sites]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("site asset ids must be unique")
        if set(self.dsm_classification) != set(asset_ids):
            raise ValueError("dsm_classification must cover exactly the declared sites")
        ppa_ids = [p.ppa_id for p in self.ppa_terms]
        if len(ppa_ids) != len(set(ppa_ids)):
            raise ValueError("ppa ids must be unique")
        obligation_ids = [o.obligation_id for o in self.obligations]
        if len(obligation_ids) != len(set(obligation_ids)):
            raise ValueError("obligation ids must be unique")
        return self


class PortfolioFixture(RecordModel):
    fixture_set_version: str
    fixture_start_utc: dt.datetime
    slot_count: Literal[96] = SLOT_COUNT
    dt_hours: Literal[0.25] = DT_HOURS
    seed: int
    assets: list[AssetSpec]
    profiles_mw: dict[str, list[float]]
    availability_profiles: dict[str, list[float]]
    dr_contracts: list[DRContract]
    market_commitments: list[MarketCommitment] = Field(default_factory=list)
    market_intents: list[MarketIntent] = Field(default_factory=list)
    initial_flow_mw: float = 0.0
    fixture_set_hash: str | None = None
    provenance: Provenance

    _start = field_validator("fixture_start_utc")(_utc)
    _ids = field_validator("seed")

    @field_validator("seed")
    @classmethod
    def signed_seed(cls, value: int) -> int:
        if value < -(2**63) or value > 2**63 - 1:
            raise ValueError("seed must be a signed 64-bit integer")
        return value

    @field_validator("fixture_set_hash")
    @classmethod
    def fixture_hash(cls, value: str | None) -> str | None:
        return None if value is None else _hash(value)

    @model_validator(mode="after")
    def validate_roster(self):
        ids = [asset.asset_id for asset in self.assets]
        if len(ids) != len(set(ids)):
            raise ValueError("asset IDs must be unique")
        if sum(isinstance(asset, SolarFarmSpec) for asset in self.assets) != 5:
            raise ValueError("fixture must contain exactly five solar farms")
        if sum(isinstance(asset, WindFarmSpec) for asset in self.assets) != 3:
            raise ValueError("fixture must contain exactly three wind farms")
        if sum(isinstance(asset, BessSpec) for asset in self.assets) != 2:
            raise ValueError("fixture must contain exactly two BESSs")
        if sum(isinstance(asset, LoadClassSpec) for asset in self.assets) != 3:
            raise ValueError("fixture must contain exactly three load classes")
        for profile in [*self.profiles_mw.values(), *self.availability_profiles.values()]:
            if len(profile) != SLOT_COUNT:
                raise ValueError("every fixture profile must contain 96 slots")
        _utc(self.fixture_start_utc)
        return self

    @model_validator(mode="after")
    def market_intents_derived(self):
        from .market import intent_from_commitment

        expected = [intent_from_commitment(c) for c in sorted(self.market_commitments, key=lambda c: c.commitment_id)]
        if self.market_intents != expected:
            raise ValueError("market_intents must equal the list derived from market_commitments")
        return self

    def asset_map(self) -> dict[str, AssetSpec]:
        return {asset.asset_id: asset for asset in self.assets}

    def assets_of(self, kind: str) -> list[AssetSpec]:
        return sorted((asset for asset in self.assets if asset.kind == kind), key=lambda asset: asset.asset_id)


class WeatherContext(RecordModel):
    regime: Literal["clear", "monsoon", "cloudy", "unknown"]
    context_timestamp: dt.datetime
    source_version: str
    quality: Literal["fresh", "stale", "missing", "conflict"]
    provenance: Provenance

    _timestamp = field_validator("context_timestamp")(_utc)


class TelemetryRecord(RecordModel):
    telemetry_id: str
    asset_id: str
    signal: SignalName
    value: float | None
    unit: Unit
    observed_at: dt.datetime
    received_at: dt.datetime
    quality: QualityFlag
    uncertainty_lower: float | None = None
    uncertainty_upper: float | None = None
    provenance: Provenance

    _ids = field_validator("telemetry_id", "asset_id")(_id)
    _observed = field_validator("observed_at", "received_at")(_utc)

    @model_validator(mode="after")
    def telemetry_rules(self):
        expected = {
            "power_mw": "MW", "energy_mwh": "MWh", "soc_fraction": "fraction",
            "reserve_mwh": "MWh", "price_usd_per_mwh": "USD_per_MWh", "carbon_kgco2e": "kgCO2e",
        }[self.signal]
        if self.unit != expected:
            raise ValueError(f"signal {self.signal} requires unit {expected}")
        if self.quality == "missing" and self.value is not None:
            raise ValueError("missing telemetry must have null value")
        if self.value is None and self.quality not in {"missing", "invalid"}:
            raise ValueError("null telemetry requires missing or invalid quality")
        if self.uncertainty_lower is not None and self.uncertainty_upper is not None and self.uncertainty_lower > self.uncertainty_upper:
            raise ValueError("uncertainty bounds are reversed")
        return self


class ForecastBand(RecordModel):
    forecast_id: str
    variable: Literal["solar", "wind", "demand", "price", "weather"]
    asset_id: str | None
    issue_time: dt.datetime
    valid_start: dt.datetime
    valid_end: dt.datetime
    unit: Unit
    p10: list[float]
    p50: list[float]
    p90: list[float]
    model_version: str
    scenario_lineage: str
    provenance: Provenance

    _ids = field_validator("forecast_id", "asset_id")(_id)
    _times = field_validator("issue_time", "valid_start", "valid_end")(_utc)

    @model_validator(mode="after")
    def forecast_rules(self):
        if self.valid_end <= self.valid_start:
            raise ValueError("forecast valid interval must be positive")
        if not len(self.p10) == len(self.p50) == len(self.p90):
            raise ValueError("forecast quantile arrays must have equal length")
        if not all(a <= b <= c for a, b, c in zip(self.p10, self.p50, self.p90)):
            raise ValueError("forecast quantiles must be ordered")
        return self


class RenewableGenerationRecord(RecordModel):
    asset_id: str
    slot_index: int = Field(ge=0, le=95)
    available_mw: float = Field(ge=0)
    dispatch_mw: float = Field(ge=0)
    curtailment_mw: float = Field(ge=0)
    quality: QualityFlag
    provenance: Provenance

    _asset = field_validator("asset_id")(_id)

    @model_validator(mode="after")
    def generation_rules(self):
        if self.dispatch_mw > self.available_mw + BESS_NUMERIC_TOLERANCE_MWH:
            raise ValueError("dispatch cannot exceed available generation")
        if abs(self.available_mw - self.dispatch_mw - self.curtailment_mw) > 0.00001:
            raise ValueError("available = dispatch + curtailment")
        return self


class DemandRecord(RecordModel):
    load_id: str
    slot_index: int = Field(ge=0, le=95)
    gross_mw: float = Field(ge=0)
    critical: bool
    quality: QualityFlag
    provenance: Provenance

    _load = field_validator("load_id")(_id)


class PriceRecord(RecordModel):
    slot_index: int = Field(ge=0, le=95)
    price_usd_per_mwh: float = Field(ge=0)
    quality: QualityFlag
    provenance: Provenance


class CarbonRecord(RecordModel):
    slot_index: int = Field(ge=0, le=95)
    carbon_kgco2e_per_mwh: float = Field(ge=0)
    provenance: Provenance


class QualityDecision(RecordModel):
    replay_id: str
    slot_index: int = Field(ge=0, le=95)
    signal: str
    signal_class: Literal["critical", "safety_relevant", "noncritical", "economic", "contextual"]
    quality: QualityFlag
    observed_at: dt.datetime | None
    replay_time: dt.datetime
    age_seconds: float | None
    code: str
    use_fallback: bool
    blocks_action: bool
    fallback_description: str | None
    provenance: Provenance

    _ids = field_validator("replay_id", "signal")(_id)
    _times = field_validator("observed_at", "replay_time")(_utc)


class TriageReport(RecordModel):
    replay_id: str
    slot_index: int = Field(ge=0, le=95)
    classification: Literal["normal", "degraded", "outage", "forecast_revision", "operator_attention"]
    severity: Literal["info", "warning", "critical"]
    affected_ids: list[str]
    codes: list[str]
    recommended_tools: list[Literal["get_quality", "record_event", "request_forecast", "request_replan"]]
    provenance: Provenance

    _replay = field_validator("replay_id")(_id)
    _affected = field_validator("affected_ids")(_unique_ids)


class ScenarioPath(RecordModel):
    scenario_id: str
    seed: int
    factor_order: list[Literal["weather", "wind", "demand", "price"]]
    weather_shock: list[float]
    wind_shock: list[float]
    demand_multiplier: list[float]
    price_shock: list[float]
    renewable_available_mw: dict[str, list[float]]
    demand_mw: dict[str, list[float]]
    price_usd_per_mwh: list[float]
    path_sha256: str | None = None
    provenance: Provenance

    _scenario = field_validator("scenario_id")(_id)
    _path = field_validator("path_sha256")(_hash)

    @model_validator(mode="after")
    def path_lengths(self):
        arrays = [self.weather_shock, self.wind_shock, self.demand_multiplier, self.price_shock, self.price_usd_per_mwh]
        arrays.extend(self.renewable_available_mw.values())
        arrays.extend(self.demand_mw.values())
        if any(len(array) != SLOT_COUNT for array in arrays):
            raise ValueError("scenario arrays must contain 96 slots")
        if self.factor_order != ["weather", "wind", "demand", "price"]:
            raise ValueError("factor order is fixed")
        return self


class ScenarioSet(RecordModel):
    method_version: str
    matrix: list[list[float]]
    matrix_sha256: str
    matrix_psd_tolerance: float
    seed: int
    scenario_ids: list[str]
    realized_path_id: str
    realized_path_sha256: str
    paths: list[ScenarioPath]
    provenance: Provenance

    _matrix_hash = field_validator("matrix_sha256", "realized_path_sha256")(_hash)

    @model_validator(mode="after")
    def matrix_rules(self):
        if len(self.matrix) != 4 or any(len(row) != 4 for row in self.matrix):
            raise ValueError("scenario correlation matrix must be 4x4")
        if len(self.paths) != len(self.scenario_ids) or self.realized_path_id not in self.scenario_ids:
            raise ValueError("scenario IDs and realized path must agree")
        return self


class BessState(RecordModel):
    asset_id: str
    energy_mwh: float = Field(ge=0)
    capacity_mwh: float = Field(gt=0)
    reserve_mwh: float = Field(ge=0)
    soc_fraction: float | None = None
    available: bool = True
    wear_throughput_mwh: float = Field(default=0, ge=0)
    provenance: Provenance

    _asset = field_validator("asset_id")(_id)

    @model_validator(mode="after")
    def derive_soc_and_bounds(self):
        if self.energy_mwh > self.capacity_mwh + BESS_NUMERIC_TOLERANCE_MWH:
            raise ValueError("BESS energy exceeds capacity")
        if self.energy_mwh + BESS_NUMERIC_TOLERANCE_MWH < self.reserve_mwh:
            raise ValueError("BESS energy is below reserve")
        derived = self.energy_mwh / self.capacity_mwh
        if self.soc_fraction is not None and abs(self.soc_fraction - derived) > BESS_NUMERIC_TOLERANCE_MWH:
            raise ValueError("soc_fraction must be derived from energy_mwh")
        object.__setattr__(self, "soc_fraction", derived)
        return self


class GridState(RecordModel):
    import_mw: float = Field(ge=0)
    export_mw: float = Field(ge=0)
    flow_mw: float
    import_limit_mw: float = Field(ge=0)
    export_limit_mw: float = Field(ge=0)
    tie_capacity_mw: float = Field(ge=0)
    tie_ramp_mw_per_slot: float = Field(ge=0)
    previous_flow_mw: float
    provenance: Provenance

    @model_validator(mode="after")
    def flow_rules(self):
        if abs(self.flow_mw - (self.import_mw - self.export_mw)) > BESS_NUMERIC_TOLERANCE_MWH:
            raise ValueError("flow must equal import minus export")
        if self.import_mw > self.import_limit_mw + BESS_NUMERIC_TOLERANCE_MWH:
            raise ValueError("import exceeds limit")
        if self.export_mw > self.export_limit_mw + BESS_NUMERIC_TOLERANCE_MWH:
            raise ValueError("export exceeds limit")
        if abs(self.flow_mw) > self.tie_capacity_mw + BESS_NUMERIC_TOLERANCE_MWH:
            raise ValueError("flow exceeds tie capacity")
        if abs(self.flow_mw - self.previous_flow_mw) > self.tie_ramp_mw_per_slot + BESS_NUMERIC_TOLERANCE_MWH:
            raise ValueError("flow exceeds tie ramp")
        return self


class ToolCall(RecordModel):
    tool_name: Literal["get_quality", "record_event", "request_forecast", "request_replan", "validate_proposal", "write_certificate"]
    request_id: str
    arguments_digest: str
    result_digest: str | None = None
    status: Literal["requested", "completed", "rejected", "unavailable"]
    provenance: Provenance

    _request = field_validator("request_id")(_id)
    _hashes = field_validator("arguments_digest", "result_digest")(_hash)


class StageOptimum(RecordModel):
    stage_name: str
    objective_name: str
    value: float
    unit: Unit | None
    fixed_tolerance: float = Field(ge=0)
    provenance: Provenance


class SolverResult(RecordModel):
    status: Literal["OPTIMAL", "INFEASIBLE", "TIME_LIMIT", "ERROR", "UNAVAILABLE", "STDLIB_FALLBACK"]
    solver_name: str
    solver_version: str
    budget_seconds: float = Field(ge=0)
    objective_values: list[float]
    stage_optima: list[StageOptimum]
    message: str
    fallback_reason: str | None = None
    method_hash: str
    provenance: Provenance

    _method = field_validator("method_hash")(_hash)


class Violation(RecordModel):
    code: str
    severity: Literal["warning", "error", "fatal"]
    field_path: str
    constraint: str
    measured_value: float | str | None
    limit_value: float | str | None
    unit: Unit | None
    recoverable: bool
    provenance: Provenance


class ActionProposal(RecordModel):
    proposal_id: str
    replay_id: str
    decision_id: str
    commit_slot: int = Field(ge=0, le=95)
    horizon_slots: Literal[1] = 1
    dt_hours: Literal[0.25] = DT_HOURS
    renewable_dispatch_mw: dict[str, list[float]]
    bess_charge_mw: dict[str, list[float]]
    bess_discharge_mw: dict[str, list[float]]
    grid_import_mw: list[float]
    grid_export_mw: list[float]
    shed_mw: dict[str, list[float]]
    shift_down_mw: dict[str, list[float]]
    shift_up_mw: dict[str, list[float]]
    rebound_mw: dict[str, list[float]]
    # F3: a distinct, UNPRICED voluntary non-critical-load reduction lever, keyed
    # by load id with a single-slot list. DEFAULTED so the fallback proposal
    # (which omits it) still validates; an empty dict means "no reduction".
    reduce_mw: dict[str, list[float]] = Field(default_factory=dict)
    market_intents: list[MarketIntent] = Field(default_factory=list)
    policy_mode: PolicyMode
    solver_result: SolverResult
    provenance: Provenance
    expires_at: dt.datetime

    _ids = field_validator("proposal_id", "replay_id", "decision_id")(_id)
    _expires = field_validator("expires_at")(_utc)

    @model_validator(mode="after")
    def proposal_shapes(self):
        maps = [
            self.renewable_dispatch_mw,
            self.bess_charge_mw,
            self.bess_discharge_mw,
            self.shed_mw,
            self.shift_down_mw,
            self.shift_up_mw,
            self.rebound_mw,
            self.reduce_mw,
        ]
        for mapping in maps:
            for key, values in mapping.items():
                _id(key)
                if len(values) != 1:
                    raise ValueError("proposal maps must contain exactly one slot")
                if values[0] < -BESS_NUMERIC_TOLERANCE_MWH:
                    raise ValueError("action values must be non-negative")
        if len(self.grid_import_mw) != 1 or len(self.grid_export_mw) != 1:
            raise ValueError("proposal directional arrays must contain one slot")
        if self.grid_import_mw[0] < -BESS_NUMERIC_TOLERANCE_MWH or self.grid_export_mw[0] < -BESS_NUMERIC_TOLERANCE_MWH:
            raise ValueError("grid actions must be non-negative")
        for bess_id in set(self.bess_charge_mw) | set(self.bess_discharge_mw):
            charge = self.bess_charge_mw.get(bess_id, [0.0])[0]
            discharge = self.bess_discharge_mw.get(bess_id, [0.0])[0]
            if charge > BESS_NUMERIC_TOLERANCE_MWH and discharge > BESS_NUMERIC_TOLERANCE_MWH:
                raise ValueError("BESS cannot charge and discharge simultaneously")
        return self


class ValidationRequest(RecordModel):
    replay_id: str
    expected_state_digest: str
    proposal: ActionProposal
    idempotency_key: str
    approval_required: bool = True

    _ids = field_validator("replay_id", "idempotency_key")(_id)
    _digest = field_validator("expected_state_digest")(_hash)


class SafetyDecision(RecordModel):
    decision_id: str
    replay_id: str
    slot_index: int = Field(ge=0, le=95)
    status: Literal["ACCEPTED", "REJECTED", "ESCALATED_NO_SAFE_ACTION"]
    approved: bool
    critical_unserved_mwh: float = Field(ge=0)
    reserve_shortfall_mwh: float = Field(ge=0)
    violations: list[Violation]
    recovery_options: list[str]
    validator_version: str
    proposal_digest: str
    provenance: Provenance

    _ids = field_validator("decision_id", "replay_id")(_id)
    _digest = field_validator("proposal_digest")(_hash)

    @model_validator(mode="after")
    def zero_slack(self):
        if self.critical_unserved_mwh > BESS_NUMERIC_TOLERANCE_MWH or self.reserve_shortfall_mwh > BESS_NUMERIC_TOLERANCE_MWH:
            if self.status != "ESCALATED_NO_SAFE_ACTION" or self.approved:
                raise ValueError("positive critical/reserve slack requires escalation and no approval")
        if self.status == "ACCEPTED" and self.approved:
            raise ValueError("validator acceptance cannot imply human approval")
        if self.status != "ACCEPTED" and self.approved:
            raise ValueError("only a separately approved, zero-slack decision may be applied")
        return self


class ApprovalRequest(RecordModel):
    replay_id: str
    decision_id: str
    expected_state_digest: str
    actor_id: str
    action: Literal["approve", "reject", "override", "escalate"]
    reason: str | None = None
    idempotency_key: str

    _ids = field_validator("replay_id", "decision_id", "actor_id", "idempotency_key")(_id)
    _digest = field_validator("expected_state_digest")(_hash)

    @model_validator(mode="after")
    def reason_for_action(self):
        if not self.reason:
            raise ValueError("an approval reason is required for every approval action")
        if self.actor_id in RESERVED_ACTOR_IDS:
            raise ValueError("the batch-replay synthetic actor cannot approve through the interactive API")
        return self


class ApprovalRecord(RecordModel):
    replay_id: str
    decision_id: str
    actor_id: str
    state: Literal["PENDING", "APPROVED", "REJECTED", "ESCALATED", "APPLIED"]
    action: Literal["approve", "reject", "override", "escalate"] | None
    reason: str | None
    expected_state_digest: str
    approval_digest: str
    provenance: Provenance

    _ids = field_validator("replay_id", "decision_id", "actor_id")(_id)
    _hashes = field_validator("expected_state_digest", "approval_digest")(_hash)


class ApprovalEvent(RecordModel):
    """Immutable human approval evidence required before twin application."""

    replay_id: str
    decision_id: str
    slot_index: int = Field(ge=0, le=95)
    actor_id: str
    reason: str
    synthetic_approval: bool
    proposal_digest: str
    current_state_digest: str
    state: Literal["APPROVED", "APPLIED"] = "APPROVED"
    provenance: Provenance

    _ids = field_validator("replay_id", "decision_id", "actor_id")(_id)
    _digests = field_validator("proposal_digest", "current_state_digest")(_hash)


class EventRecord(RecordModel):
    event_id: str
    replay_id: str
    slot_index: int = Field(ge=0, le=95)
    event_type: Literal["normal", "monsoon_cloud", "demand_spike", "bess_outage", "tie_derate", "stale_telemetry", "missing_telemetry", "unsafe_rejection", "replan", "approval", "recovery", "escalation", "solver", "quality"]
    start_slot: int = Field(ge=0, le=95)
    end_slot: int = Field(ge=0, le=95)
    affected_ids: list[str]
    details: dict[str, str | float | int | bool]
    provenance: Provenance

    _ids = field_validator("event_id", "replay_id")(_id)

    @model_validator(mode="after")
    def event_interval(self):
        if self.end_slot < self.start_slot:
            raise ValueError("event interval reversed")
        return self


class ReplayCreateRequest(RecordModel):
    fixture_set_hash: str
    seed: int
    policy_mode: PolicyMode
    adapter_state: Literal["DISABLED", "VAYU_CONTEXT_ONLY"] = "DISABLED"
    expected_state_digest: str | None = None
    idempotency_key: str

    _hash = field_validator("fixture_set_hash", "expected_state_digest")(_hash)
    _idempotency = field_validator("idempotency_key")(_id)

    @field_validator("seed")
    @classmethod
    def seed_range(cls, value: int) -> int:
        if not -(2**63) <= value <= 2**63 - 1:
            raise ValueError("seed must be signed 64-bit")
        return value


class ReplayAdvanceRequest(RecordModel):
    replay_id: str
    expected_slot_index: int = Field(ge=0, le=95)
    expected_state_digest: str
    idempotency_key: str
    proposal: ActionProposal | None = None
    approval_token: str | None = None

    _ids = field_validator("replay_id", "idempotency_key")(_id)
    _digest = field_validator("expected_state_digest")(_hash)


class DemoRequest(RecordModel):
    seed: int = 20260701
    policy_mode: PolicyMode = "balanced"
    adapter_state: Literal["DISABLED", "VAYU_CONTEXT_ONLY"] = "DISABLED"
    idempotency_key: str = "demo-default"
    deadline_seconds: float = Field(default=REPLAY_BUDGET_SECONDS, gt=0, le=300)

    _idempotency = field_validator("idempotency_key")(_id)


class MetricValue(RecordModel):
    name: str
    value: float | int | None
    unit: Unit | None
    unit_justification: str | None = None
    status: Literal["available", "unavailable"]
    reason: str | None = None
    provenance: Provenance

    @model_validator(mode="after")
    def metric_rules(self):
        if self.status == "unavailable" and (self.value is not None or not self.reason):
            raise ValueError("unavailable metric requires null value and reason")
        if self.status == "available" and self.value is None:
            raise ValueError("available metric requires a value")
        if self.status == "available" and self.unit is None and not self.unit_justification:
            raise ValueError("available metric with null unit requires a unit_justification")
        return self


class Metrics(RecordModel):
    replay_id: str
    fixture_set_hash: str
    realized_path_id: str
    values: list[MetricValue]
    trace_complete: bool
    provenance: Provenance
    carbon_factor_version: str = "synthetic-carbon-factor-v1"
    carbon_factor_digest: str = "0" * 64

    _ids = field_validator("replay_id", "realized_path_id")(_id)
    _hash = field_validator("fixture_set_hash", "carbon_factor_digest")(_hash)

    @model_validator(mode="after")
    def unique_metrics(self):
        if len({metric.name for metric in self.values}) != len(self.values):
            raise ValueError("metric names must be unique")
        return self


class LatencySample(StrictModel):
    """A single measured timing. StrictModel, never a RecordModel: never hashed."""

    replay_id: str
    slot_index: int = Field(ge=0, le=95)
    phase: Literal["build", "stage", "total"]
    stage_name: str | None
    elapsed_seconds: float = Field(ge=0)
    budget_seconds: float = Field(ge=0)
    remaining_seconds: float = Field(ge=0)


class LatencyReport(StrictModel):
    """Non-deterministic measured-latency report; never enters a digest."""

    replay_id: str
    samples: list[LatencySample]
    values: list[MetricValue]


class ErrorBody(RecordModel):
    code: str
    message: str
    field_errors: dict[str, str] = Field(default_factory=dict)
    replay_id: str | None = None
    slot_index: int | None = Field(default=None, ge=0, le=95)
    recoverable: bool = False
    retryable: bool = False


T = TypeVar("T")


class ResponseEnvelope(StrictModel, Generic[T]):
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    request_id: str
    status: Literal["ok", "rejected", "error"]
    data: T | None = None
    error: ErrorBody | None = None
    replay_scope: Literal["SIMULATION", "REPLAY"] = "REPLAY"

    _request = field_validator("request_id")(_id)

    @model_validator(mode="after")
    def one_payload(self):
        if (self.data is None) == (self.error is None):
            raise ValueError("response must contain exactly one of data or error")
        if self.status == "ok" and self.error is not None:
            raise ValueError("ok response cannot contain error")
        if self.status in {"rejected", "error"} and self.data is not None and self.status == "error":
            raise ValueError("error response cannot contain success data")
        return self


class Certificate(RecordModel):
    certificate_id: str
    replay_id: str
    decision_id: str
    decision_status: Literal["ACCEPTED", "REJECTED", "FALLBACK", "ESCALATED", "OVERRIDDEN"]
    unsigned_digest: str
    input_manifest: dict[str, str]
    evidence: dict[str, Any]
    key_id: str
    key_fingerprint: str
    signature_base64: str
    signature_algorithm: Literal["Ed25519"] = "Ed25519"
    runtime_metadata: dict[str, str] = Field(default_factory=dict)

    _ids = field_validator("certificate_id", "replay_id", "decision_id", "key_id")(_id)
    _hashes = field_validator("unsigned_digest", "key_fingerprint")(_hash)


class BenchSignal(StrictModel):
    name: SignalName
    value: float | None
    unit: Unit
    quality: QualityFlag
    observed_slot: int = Field(ge=0, le=95)


class BenchTelemetry(RecordModel):
    endpoint_id: str
    sequence: int = Field(ge=0)
    replay_id: str
    slot_index: int = Field(ge=0, le=95)
    state_digest: str
    signals: list[BenchSignal]
    clock_source: Literal["replay_clock"] = "replay_clock"
    provenance: Provenance

    _ids = field_validator("endpoint_id", "replay_id")(_id)
    _digest = field_validator("state_digest")(_hash)


class BenchCommand(RecordModel):
    command_id: str
    endpoint_id: str
    proposal_id: str
    sequence: int = Field(ge=1)
    issued_slot: int = Field(ge=0, le=95)
    expires_at_slot: int = Field(ge=1, le=96)
    expected_state_digest: str
    charge_mw: float = Field(ge=0)
    discharge_mw: float = Field(ge=0)
    import_mw: float = Field(ge=0)
    export_mw: float = Field(ge=0)
    validator_approved: bool
    provenance: Provenance

    _ids = field_validator("command_id", "endpoint_id", "proposal_id")(_id)
    _digest = field_validator("expected_state_digest")(_hash)

    @model_validator(mode="after")
    def command_rules(self):
        if self.expires_at_slot <= self.issued_slot:
            raise ValueError("command must expire after issued slot")
        if self.charge_mw > 0 and self.discharge_mw > 0:
            raise ValueError("bench command cannot charge and discharge")
        if not self.validator_approved:
            raise ValueError("bench command requires validator approval")
        return self


class BenchAck(RecordModel):
    command_id: str
    endpoint_id: str
    sequence: int = Field(ge=1)
    status: Literal["ACKED", "REJECTED", "LATE"]
    acknowledged_wall_seconds: float = Field(ge=0)
    state_digest: str
    provenance: Provenance

    _ids = field_validator("command_id", "endpoint_id")(_id)
    _digest = field_validator("state_digest")(_hash)


class BenchFault(RecordModel):
    endpoint_id: str
    fault_code: Literal["EXPIRED", "DUPLICATE_SEQUENCE", "BAD_DIGEST", "OUT_OF_RANGE", "RAMP", "DWELL", "ACK_TIMEOUT", "DISCONNECT"]
    slot_index: int = Field(ge=0, le=95)
    message: str
    safe_state_applied: bool
    provenance: Provenance

    _endpoint = field_validator("endpoint_id")(_id)


class CapabilityBound(StrictModel):
    min_value: float
    max_value: float
    ramp_mw_per_slot: float = Field(ge=0)
    dwell_slots: int = Field(ge=1)


class BenchCapability(RecordModel):
    endpoint_id: str
    protocol_version: Literal["bench-mock-1"]
    clock_source: Literal["replay_clock"]
    telemetry_signals: list[SignalName]
    command_capabilities: dict[str, CapabilityBound]
    safe_state: dict[str, float | bool]
    ack_timeout_seconds: float = 2.0
    disconnect_policy: Literal["SAFE_DISCONNECTED"] = "SAFE_DISCONNECTED"
    provenance: Provenance

    _endpoint = field_validator("endpoint_id")(_id)


class BenchState(RecordModel):
    endpoint_id: str
    state: Literal["DISABLED", "READY", "COMMAND_SENT", "ACKED", "SAFE_DISCONNECTED"]
    previous_output: dict[str, float | bool]
    current_output: dict[str, float | bool]
    last_sequence: int = Field(ge=0)
    last_slot: int = Field(ge=0, le=95)
    state_digest: str
    provenance: Provenance

    _endpoint = field_validator("endpoint_id")(_id)
    _digest = field_validator("state_digest")(_hash)


class ReplayState(RecordModel):
    replay_id: str
    slot_index: int = Field(ge=0, le=96)
    state_digest: str
    portfolio: PortfolioFixture
    scenario_set: ScenarioSet | None
    bess_states: dict[str, BessState]
    grid_state: GridState
    pending_approval: ApprovalRecord | None
    applied_event_ids: list[str]
    adapter_state: Literal["DISABLED", "VAYU_CONTEXT_ONLY"]
    provenance: Provenance

    _replay = field_validator("replay_id")(_id)
    _digest = field_validator("state_digest")(_hash)


class DemoSummary(RecordModel):
    replay_id: str
    baseline_metrics: Metrics | None = None
    slot_count: int
    policy_mode: PolicyMode
    realized_path_id: str
    realized_path_sha256: str
    planner_status: str
    planned_fallback_count: int
    unsafe_rejections: int
    final_safety_status: str
    adapter_state: str
    metrics: Metrics
    certificate_path: str
    provenance: Provenance


class QualityGateReport(RecordModel):
    replay_id: str
    slot_index: int = Field(ge=0, le=95)
    decisions: list[QualityDecision]
    triage: TriageReport
    blocks_action: bool
    use_fallback: bool
    provenance: Provenance

    _replay = field_validator("replay_id")(_id)


class TwinSlotState(RecordModel):
    replay_id: str
    slot_index: int = Field(ge=0, le=95)
    timestamp_utc: dt.datetime
    renewable_available_mw: float = Field(ge=0)
    renewable_dispatch_mw: float = Field(ge=0)
    demand_mw: float = Field(ge=0)
    import_mw: float = Field(ge=0)
    export_mw: float = Field(ge=0)
    flow_mw: float
    bess_energy_mwh: dict[str, float]
    bess_soc_fraction: dict[str, float]
    reserve_shortfall_mwh: float = Field(ge=0)
    served_mwh: float = Field(ge=0)
    critical_unserved_mwh: float = Field(ge=0)
    noncritical_unserved_mwh: float = Field(ge=0)
    curtailment_mwh: float = Field(ge=0)
    grid_import_cost_usd: float = Field(ge=0)
    market_revenue_usd: float = Field(ge=0)
    nonmarket_export_valuation_usd: float = Field(ge=0)
    market_shortfall_mwh: float = Field(ge=0)
    market_shortfall_penalty_usd: float = Field(ge=0)
    committed_export_mw: float = Field(ge=0)
    uncommitted_export_mw: float = Field(ge=0)
    dr_shed_mwh: float = Field(ge=0)
    dr_shift_mwh: float = Field(ge=0)
    dr_rebound_mwh: float = Field(ge=0)
    dr_cost_usd: float = Field(ge=0)
    cost_usd: float
    carbon_kgco2e: float = Field(ge=0)
    event_ids: list[str]
    action_applied: bool
    proposal_id: str | None
    decision_id: str | None
    provenance: Provenance

    _replay = field_validator("replay_id")(_id)
    _timestamp = field_validator("timestamp_utc")(_utc)


class PlanResult(RecordModel):
    replay_id: str
    slot_index: int = Field(ge=0, le=95)
    policy_mode: PolicyMode
    proposal: ActionProposal | None
    solver_result: SolverResult
    fallback_used: bool
    fallback_reason: str | None
    trace: PlanningTrace | None = None
    provenance: Provenance

    _replay = field_validator("replay_id")(_id)


class PlanningTrace(RecordModel):
    trace_id: str
    replay_id: str
    slot_index: int = Field(ge=0, le=95)
    tool_calls: list[ToolCall]
    objective_sequence: list[str]
    stage_optima: list[StageOptimum]
    proposal_id: str | None
    status: Literal["completed", "fallback", "escalated"]
    provenance: Provenance

    _ids = field_validator("trace_id", "replay_id")(_id)


class ReplayResult(RecordModel):
    replay_id: str
    seed: int
    policy_mode: PolicyMode
    scenario_set: ScenarioSet
    slot_states: list[TwinSlotState]
    metrics: Metrics
    decisions: list[SafetyDecision]
    events: list[EventRecord]
    adapter_state: Literal["DISABLED", "VAYU_CONTEXT_ONLY"]
    provenance: Provenance

    _replay = field_validator("replay_id")(_id)


class PlanningRequest(RecordModel):
    replay_id: str
    expected_state_digest: str
    slot_index: int = Field(ge=0, le=95)
    policy_mode: PolicyMode
    idempotency_key: str

    _ids = field_validator("replay_id", "idempotency_key")(_id)
    _digest = field_validator("expected_state_digest")(_hash)


class CertificateVerifyRequest(RecordModel):
    certificate_id: str
    expected_state_digest: str | None = None
    idempotency_key: str

    _ids = field_validator("certificate_id", "idempotency_key")(_id)
    _digest = field_validator("expected_state_digest")(_hash)


# Export a reusable adapter for API/tests that need the union's exact schema.
ASSET_ADAPTER = TypeAdapter(AssetSpec)
