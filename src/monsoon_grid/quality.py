"""Replay-clock data-quality and triage gate."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Iterable, Mapping

from .config import SLOT_MINUTES
from .contracts import PortfolioFixture, Provenance, QualityDecision, QualityFlag, TelemetryRecord, TriageReport

SIGNAL_POLICY_TABLE = {
    "bess_energy": {"signal_class": "critical", "max_age_seconds": 900, "missing_code": "DATA_QUALITY_BLOCKED", "fallback": "last_validated_state"},
    "bess_reserve": {"signal_class": "critical", "max_age_seconds": 900, "missing_code": "DATA_QUALITY_BLOCKED", "fallback": "last_validated_state"},
    "grid_limits": {"signal_class": "critical", "max_age_seconds": 900, "missing_code": "DATA_QUALITY_BLOCKED", "fallback": "conservative_minimum_known_limit"},
    "tie_line_limits": {"signal_class": "critical", "max_age_seconds": 900, "missing_code": "DATA_QUALITY_BLOCKED", "fallback": "conservative_minimum_known_limit"},
    "critical_load": {"signal_class": "critical", "max_age_seconds": 900, "missing_code": "DATA_QUALITY_BLOCKED", "fallback": None},
    "solar_availability": {"signal_class": "safety_relevant", "max_age_seconds": 1800, "missing_code": "DEGRADED_QUALITY", "fallback": "zero_availability"},
    "wind_availability": {"signal_class": "safety_relevant", "max_age_seconds": 1800, "missing_code": "DEGRADED_QUALITY", "fallback": "zero_availability"},
    "flexible_noncritical_load": {"signal_class": "noncritical", "max_age_seconds": 3600, "missing_code": "DEGRADED_QUALITY", "fallback": "conservative_no_dr"},
    "price": {"signal_class": "economic", "max_age_seconds": 3600, "missing_code": "DEGRADED_QUALITY", "fallback": "no_market"},
    "weather_context": {"signal_class": "contextual", "max_age_seconds": 21600, "missing_code": "DEGRADED_QUALITY", "fallback": "unknown_context"},
}


@dataclass(frozen=True)
class QualityGateResult:
    decisions: list[QualityDecision]
    triage: TriageReport
    blocks_action: bool
    use_fallback: bool


def replay_time(fixture_start: dt.datetime, slot_index: int) -> dt.datetime:
    return fixture_start.astimezone(dt.timezone.utc) + dt.timedelta(minutes=SLOT_MINUTES * slot_index)


def _signal_policy(signal: str) -> dict[str, object]:
    if signal in SIGNAL_POLICY_TABLE:
        return SIGNAL_POLICY_TABLE[signal]
    if signal.startswith("solar"):
        return SIGNAL_POLICY_TABLE["solar_availability"]
    if signal.startswith("wind"):
        return SIGNAL_POLICY_TABLE["wind_availability"]
    if signal.startswith("bess"):
        return SIGNAL_POLICY_TABLE["bess_energy"]
    if signal in {"shiftable_process", "deferrable_aux", "flexible_load"}:
        return SIGNAL_POLICY_TABLE["flexible_noncritical_load"]
    return SIGNAL_POLICY_TABLE["weather_context"]


def evaluate_quality(
    portfolio: PortfolioFixture,
    *,
    replay_id: str,
    slot_index: int,
    telemetry: Iterable[TelemetryRecord] = (),
    signals: Iterable[str] | None = None,
    signal_records: Mapping[str, TelemetryRecord | None] | None = None,
) -> QualityGateResult:
    now = replay_time(portfolio.fixture_start_utc, slot_index)
    telemetry = list(telemetry)
    grouped: dict[str, list[TelemetryRecord]] = {}
    for record in telemetry:
        grouped.setdefault(record.signal, []).append(record)
    conflicting_keys = {
        key
        for key, records in grouped.items()
        if len(records) > 1 and len({(record.value, record.quality) for record in records}) > 1
    }

    def find_record(signal_name: str):
        if signal_records is not None and signal_name in signal_records:
            return signal_records[signal_name]
        direct = grouped.get(signal_name, [])
        if direct:
            return direct[-1]
        prefixes = {
            "solar_availability": ("solar-",),
            "wind_availability": ("wind-",),
            "bess_energy": ("bess-",),
            "bess_reserve": ("bess-",),
            "flexible_noncritical_load": ("shiftable_process", "deferrable_aux"),
        }.get(signal_name, ())
        return next((record for record in telemetry if record.asset_id.startswith(prefixes) or record.asset_id == signal_name or record.telemetry_id == signal_name), None)
    signal_names = list(signals or ["bess_energy", "bess_reserve", "grid_limits", "tie_line_limits", "critical_load", "solar_availability", "wind_availability", "flexible_noncritical_load", "price", "weather_context"])
    decisions: list[QualityDecision] = []
    affected: list[str] = []
    codes: list[str] = []
    blocking = False
    fallback = False
    for signal in signal_names:
        policy = _signal_policy(signal)
        record = find_record(signal)
        age_seconds: float | None = None
        quality: QualityFlag = "missing"
        code = str(policy["missing_code"])
        fallback_description = policy["fallback"]
        if record is not None:
            observed = record.observed_at.astimezone(dt.timezone.utc)
            age_seconds = (now - observed).total_seconds()
            if record.signal in conflicting_keys:
                quality = "conflict"
                code = "CONFLICTING_SIGNAL"
                blocking = blocking or policy["signal_class"] in {"critical", "safety_relevant"}
            elif age_seconds < 0:
                quality = "invalid"
                code = "FUTURE_OBSERVATION_REJECTED"
                blocking = blocking or policy["signal_class"] == "critical"
            elif record.quality == "conflict":
                quality = "conflict"
                code = "CONFLICTING_SIGNAL"
                blocking = blocking or policy["signal_class"] in {"critical", "safety_relevant"}
            elif record.quality in {"missing", "invalid"}:
                quality = record.quality
                code = str(policy["missing_code"])
            elif age_seconds <= float(policy["max_age_seconds"]):
                quality = "fresh"
                code = "QUALITY_OK"
                fallback_description = None
            else:
                quality = "stale"
                code = "DATA_QUALITY_BLOCKED" if policy["signal_class"] == "critical" else str(policy["missing_code"])
                blocking = blocking or policy["signal_class"] == "critical"
            if quality in {"missing", "invalid", "conflict", "stale"}:
                affected.append(record.asset_id)
                codes.append(code)
        else:
            if policy["signal_class"] == "critical":
                blocking = True
            affected.append(signal)
            codes.append(code)
        use_fallback = quality != "fresh"
        fallback = fallback or use_fallback
        provenance = Provenance(label="derived", source_id="quality-gate", source_version="quality-table-v1", input_refs=[record.record_id or record.telemetry_id] if record else ["quality-policy"])
        decisions.append(QualityDecision(
            replay_id=replay_id,
            slot_index=slot_index,
            signal=signal,
            signal_class=policy["signal_class"],
            quality=quality,
            observed_at=record.observed_at if record else None,
            replay_time=now,
            age_seconds=age_seconds,
            code=code,
            use_fallback=use_fallback,
            blocks_action=blocking if policy["signal_class"] == "critical" else False,
            fallback_description=str(fallback_description) if fallback_description is not None else None,
            provenance=provenance,
        ))
    severity = "critical" if blocking else ("warning" if fallback else "info")
    classification = "operator_attention" if blocking else ("degraded" if fallback else "normal")
    triage = TriageReport(
        replay_id=replay_id,
        slot_index=slot_index,
        classification=classification,
        severity=severity,
        affected_ids=sorted(set(affected)),
        codes=sorted(set(codes)),
        recommended_tools=["get_quality", "record_event"] + (["request_replan"] if fallback else []),
        provenance=Provenance(label="derived", source_id="quality-gate", source_version="quality-table-v1", input_refs=[decision.record_id or decision.signal for decision in decisions]),
    )
    return QualityGateResult(decisions=decisions, triage=triage, blocks_action=blocking, use_fallback=fallback)
