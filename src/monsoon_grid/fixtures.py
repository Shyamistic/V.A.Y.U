"""Checked-in demo fixture loading and deterministic profile helpers."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .assumptions import CarbonFactorAssumption, load_carbon_factor, load_flexibility_costs
from .canonical import sha256_hex
from .config import DT_HOURS, FIXTURE_ROOT, FIXTURE_SET_FILES, SLOT_COUNT
from .contracts import DeliveryObligation, EventRecord, MarketCommitment, PortfolioFixture, Provenance, WeatherContext
from .errors import FixtureDigestError
from .market import intent_from_commitment


def fixture_provenance(source_id: str = "demo_fixture") -> Provenance:
    return Provenance(label="scenario_assumption", source_id=source_id, source_version="1", input_refs=[])


def _read(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def fixture_set_hash() -> str:
    payload = {name: _read(name) for name in FIXTURE_SET_FILES}
    return sha256_hex(payload)


def load_demo_portfolio() -> PortfolioFixture:
    raw = load_raw_portfolio()
    if not raw.get("fixture_set_hash"):
        raw["fixture_set_hash"] = fixture_set_hash()
    return PortfolioFixture.model_validate_json(json.dumps(raw))


def load_demo_events() -> list[EventRecord]:
    raw = _read("demo_events.json")
    return [EventRecord.model_validate_json(json.dumps(event)) for event in raw["events"]]


def load_demo_weather() -> WeatherContext:
    return WeatherContext.model_validate_json(json.dumps(_read("demo_weather_context.json")))


def load_demo_obligations() -> list[DeliveryObligation]:
    raw = _read("demo_obligations.json")
    return [DeliveryObligation.model_validate_json(json.dumps(entry)) for entry in raw["obligations"]]


def solar_profile(slot: int) -> float:
    if slot < 24 or slot >= 72:
        return 0.0
    if slot <= 48:
        return (slot - 24) / 24.0
    return (72 - slot) / 24.0


def wind_profile(slot: int, offset: float) -> float:
    return max(0.20, min(0.85, 0.55 + 0.10 * math.sin(2 * math.pi * slot / SLOT_COUNT) + offset))


def expected_profiles() -> dict[str, list[float]]:
    values: dict[str, list[float]] = {}
    for index in range(1, 6):
        values[f"solar-{index:02d}"] = [solar_profile(slot) for slot in range(SLOT_COUNT)]
    for index, offset in enumerate((-0.04, 0.0, 0.03), start=1):
        values[f"wind-{index:02d}"] = [wind_profile(slot, offset) for slot in range(SLOT_COUNT)]
    values["critical_process"] = [34.0] * SLOT_COUNT
    values["shiftable_process"] = [18.0 if slot < 24 or slot >= 72 else 24.0 for slot in range(SLOT_COUNT)]
    values["deferrable_aux"] = [9.0 if slot < 48 or slot >= 72 else 12.0 for slot in range(SLOT_COUNT)]
    values["price"] = [70.0 + 20.0 * math.sin(2 * math.pi * (slot - 20) / SLOT_COUNT) for slot in range(SLOT_COUNT)]
    return {key: [round(value, 6) for value in series] for key, series in values.items()}


def materialize_profiles(raw: dict[str, Any]) -> dict[str, list[float]]:
    generated = expected_profiles()
    specification = _read("demo_profiles.json")
    if specification.get("materialized_profiles_sha256") != sha256_hex(generated):
        raise ValueError("checked-in profile materialization hash does not match deterministic generator")
    for profile_id, expected_hash in specification.get("profile_sha256", {}).items():
        if profile_id in generated and sha256_hex(generated[profile_id]) != expected_hash:
            raise ValueError(f"profile hash mismatch: {profile_id}")
    profiles = raw.get("profiles_mw")
    return profiles if profiles else generated


def _validate_flexibility_costs(raw: dict[str, Any]) -> None:
    """Validate declared load-class costs against the normative fixture (A2).

    Present-and-equal is left as written; present-and-different or absent is a
    fatal ``FixtureDigestError`` so the demo portfolio always states what it means.
    """
    fixture = {entry.load_id: entry for entry in load_flexibility_costs().entries}
    for asset in raw["assets"]:
        if asset.get("kind") != "load_class":
            continue
        load_id = asset["asset_id"]
        if load_id not in fixture:
            continue
        for field, declared_key in (("shed_cost_usd_per_mwh", "shed_cost_usd_per_mwh"), ("shift_cost_usd_per_mwh", "shift_cost_usd_per_mwh")):
            if declared_key not in asset:
                raise FixtureDigestError(f"load class {load_id} is missing {declared_key}", code="FIXTURE_VALUE_MISMATCH")
            fixture_value = getattr(fixture[load_id], field)
            if abs(asset[declared_key] - fixture_value) > 1e-9:
                raise FixtureDigestError(
                    f"load class {load_id} {declared_key}={asset[declared_key]} differs from fixture {fixture_value}",
                    code="FIXTURE_VALUE_MISMATCH",
                )


def load_raw_portfolio() -> dict[str, Any]:
    raw = _read("demo_portfolio.json")
    raw["profiles_mw"] = materialize_profiles(raw)
    if not raw.get("availability_profiles"):
        raw["availability_profiles"] = {asset["asset_id"]: [1.0] * SLOT_COUNT for asset in raw["assets"]}
    _validate_flexibility_costs(raw)
    if "market_commitments" not in raw:
        raise FixtureDigestError("demo portfolio is missing market_commitments", code="FIXTURE_VALUE_MISMATCH")
    commitments = [MarketCommitment.model_validate(entry) for entry in raw["market_commitments"]]
    derived = [intent_from_commitment(c).model_dump(mode="python") for c in sorted(commitments, key=lambda c: c.commitment_id)]
    raw["market_intents"] = derived
    if not raw.get("fixture_set_hash"):
        raw["fixture_set_hash"] = fixture_set_hash()
    return raw
