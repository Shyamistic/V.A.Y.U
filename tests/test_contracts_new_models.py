"""D3: structured rejection and cross-field rules for the six new contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from monsoon_grid.contracts import (
    AvailableCapacityRecord,
    FlexibilityContract,
    MarketCommitment,
    PpaTerms,
    ScheduleRecord,
    StorageWarrantyTerms,
)


def _prov(label="scenario_assumption"):
    return {"label": label, "source_id": "test-fixture", "source_version": "v1", "input_refs": []}


def _available(**over):
    base = {"asset_id": "solar-01", "slot_index": 10, "technology": "solar", "nameplate_capacity_mw": 100.0, "available_capacity_mw": 90.0, "derate_fraction": 0.10, "outage_reason": None, "derate_reason": "thermal", "provenance": _prov()}
    base.update(over)
    return AvailableCapacityRecord.model_validate(base)


def test_available_capacity_valid():
    rec = _available()
    assert rec.available_capacity_mw == 90.0


def test_available_capacity_exceeds_nameplate_rejected():
    with pytest.raises(ValidationError):
        _available(available_capacity_mw=120.0)


def test_available_capacity_derate_requires_reason():
    with pytest.raises(ValidationError):
        _available(derate_reason=None)


def test_available_capacity_zero_requires_outage_reason():
    with pytest.raises(ValidationError):
        _available(available_capacity_mw=0.0, derate_fraction=1.0, derate_reason="x", outage_reason=None)


def test_available_capacity_extra_field_rejected():
    with pytest.raises(ValidationError):
        AvailableCapacityRecord.model_validate({"asset_id": "s", "slot_index": 0, "technology": "solar", "nameplate_capacity_mw": 10.0, "available_capacity_mw": 10.0, "derate_fraction": 0.0, "provenance": _prov(), "extra": 1})


def test_available_capacity_wrong_type_rejected():
    with pytest.raises(ValidationError):
        _available(nameplate_capacity_mw="lots")


def test_available_capacity_seven_decimals_rejected():
    with pytest.raises(ValidationError):
        _available(available_capacity_mw=90.1234567, derate_fraction=0.0, outage_reason=None, derate_reason=None, nameplate_capacity_mw=90.1234567)


def test_schedule_revision_must_supersede():
    import json

    with pytest.raises(ValidationError, match="supersede"):
        ScheduleRecord.model_validate_json(json.dumps({"schedule_id": "sch-1", "asset_id": "solar-01", "slot_index": 0, "scheduled_mw": 10.0, "submitted_at_utc": "2026-07-01T00:00:00Z", "revision_number": 2, "supersedes_revision": None, "sldc_receipt_reference": None, "provenance": _prov()}))


def test_schedule_naive_timestamp_rejected():
    with pytest.raises(ValidationError):
        ScheduleRecord.model_validate({"schedule_id": "sch-1", "asset_id": "solar-01", "slot_index": 0, "scheduled_mw": 10.0, "submitted_at_utc": "2026-07-01T00:00:00", "revision_number": 0, "supersedes_revision": None, "sldc_receipt_reference": None, "provenance": _prov()})


def test_ppa_term_order_rejected():
    import json

    with pytest.raises(ValidationError, match="term_end"):
        PpaTerms.model_validate_json(json.dumps({"ppa_id": "p1", "counterparty_id": "c1", "tariff_usd_per_mwh": 70.0, "committed_cuf_fraction": 0.3, "peak_window_slots": [], "shortfall_penalty_usd_per_mwh": 140.0, "excess_payment_fraction": 0.2, "relaxation_allowance_fraction": 0.1, "term_start_utc": "2030-01-01T00:00:00Z", "term_end_utc": "2026-01-01T00:00:00Z", "provenance": _prov()}))


def test_flexibility_unusable_contract_must_be_free():
    with pytest.raises(ValidationError):
        FlexibilityContract.model_validate({"contract_id": "f1", "load_id": "l1", "shed_cost_usd_per_mwh": 100.0, "shift_cost_usd_per_mwh": 0.0, "notice_period_slots": 1, "max_events_per_period": 0, "measurement_period_slots": 96, "rebound_obligation_mwh": 0.0, "rebound_window_slots": [], "provenance": _prov()})


def test_market_commitment_cleared_requires_quantity():
    with pytest.raises(ValidationError):
        MarketCommitment.model_validate({"commitment_id": "c1", "market_id": "m1", "submission_slot": 40, "delivery_start_slot": 44, "delivery_end_slot": 47, "quantity_mwh": 0.0, "price_usd_per_mwh": 86.0, "fee_usd_per_mwh": 1.5, "loss_fraction": 0.02, "shortfall_penalty_usd_per_mwh": 40.0, "gate_open_slot": 40, "gate_close_slot": 42, "clearing_status": "CLEARED", "provenance": _prov()})


def test_storage_warranty_throughput_consistency_against_bess():
    # throughput_budget_mwh >= cycle * capacity * DoD * 0.5 for the declared BESS.
    import json

    from monsoon_grid.fixtures import load_demo_portfolio

    warranty = StorageWarrantyTerms.model_validate_json(json.dumps({"asset_id": "bess-a", "cycle_budget_count": 6000, "throughput_budget_mwh": 480000.0, "calendar_life_years": 15.0, "max_depth_of_discharge_fraction": 0.9, "max_c_rate_charge": 0.5, "max_c_rate_discharge": 0.5, "warranty_start_utc": "2024-01-01T00:00:00Z", "provenance": _prov()}))
    bess = next(a for a in load_demo_portfolio().assets if a.asset_id == "bess-a")
    assert warranty.throughput_budget_mwh >= warranty.cycle_budget_count * bess.capacity_mwh * warranty.max_depth_of_discharge_fraction * 0.5
