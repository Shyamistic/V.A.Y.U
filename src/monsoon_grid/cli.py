"""Command-line workflows for replay, demo, inventory, service, and verification."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .assumptions import CarbonFactorAssumption, load_carbon_factor
from .baseline import run_greedy_p50_baseline
from .canonical import canonical_bytes, sha256_hex
from .certificates import create_certificate, demo_trust_environment, export_certificate, load_and_verify
from .config import ARTIFACT_ROOT, FINALIZATION_BUDGET_SECONDS, FIXTURE_ROOT, FIXTURE_SET_FILES, POLICY_OBJECTIVES, PRODUCT_ROOT, PROVENANCE_ROOT, REPLAY_BUDGET_SECONDS, REPLAY_VERSION, RUNTIME_ROOT, Settings, ensure_runtime_dirs
from .contracts import ActionProposal, ApprovalEvent, DemoSummary, EventRecord, Metrics, PlanResult, PortfolioFixture, Provenance, QualityGateReport, ReplayResult, SafetyDecision, TelemetryRecord
from .equations import bess_energy_next
from .fallback import fallback_plan
from .fixtures import fixture_set_hash, load_demo_events, load_demo_portfolio
from .forecasts import build_forecasts
from .persistence import PersistenceStore
from .planner import plan_next
from .provenance import inventory
from .quality import evaluate_quality, replay_time
from .scenarios import generate_scenarios
from .twin import TwinResult, run_twin
from .validator import validate_proposal
from .vayu_adapter import AdapterStatus, VayuContextAdapter


@dataclass
class ReplayExecution:
    replay_id: str
    portfolio: PortfolioFixture
    events: list[EventRecord]
    scenarios: Any
    forecasts: list[Any]
    quality_reports: dict[int, QualityGateReport]
    proposals: dict[int, ActionProposal]
    decisions: dict[int, SafetyDecision]
    approvals: dict[int, ApprovalEvent]
    twin: TwinResult
    baseline_twin: TwinResult
    elapsed_seconds: float
    fallback_count: int
    adapter_state: str
    adapter_status: AdapterStatus
    weather_context: Any
    carbon_factor: CarbonFactorAssumption


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="python", exclude_none=False)
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _write_canonical(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))


def _configure_demo_trust() -> None:
    """Use only the immutable, product-shipped demo anchor for local demo mode."""
    if not os.environ.get("MONSOON_GRID_TRUST_KEYRING") or not os.environ.get("MONSOON_GRID_TRUST_FINGERPRINT") or not os.environ.get("MONSOON_GRID_SIGNING_KEY"):
        for key, value in demo_trust_environment().items():
            os.environ.setdefault(key, value)


def _event_replay(events: list[EventRecord], replay_id: str) -> list[EventRecord]:
    return [event.model_copy(update={"replay_id": replay_id}) for event in events]


def _state_digest_for_approval(replay_id: str, slot: int, energy: dict[str, float], flow: float) -> str:
    return sha256_hex({"replay_id": replay_id, "slot_index": slot, "bess_energy_mwh": dict(sorted(energy.items())), "previous_flow_mw": round(flow, 6)})


def _synthetic_telemetry(portfolio: PortfolioFixture, *, replay_id: str, slot: int, events: list[EventRecord]) -> dict[str, TelemetryRecord]:
    now = replay_time(portfolio.fixture_start_utc, slot)
    provenance = Provenance(label="scenario_assumption", source_id="demo-telemetry", source_version="synthetic-telemetry-v1", input_refs=[portfolio.record_id or "portfolio"])
    signal_specs = {
        "bess_energy": ("energy_mwh", "MWh", 14.0),
        "bess_reserve": ("energy_mwh", "MWh", 4.0),
        "grid_limits": ("power_mw", "MW", 75.0),
        "tie_line_limits": ("power_mw", "MW", 80.0),
        "critical_load": ("power_mw", "MW", 34.0),
        "solar_availability": ("power_mw", "MW", 1.0),
        "wind_availability": ("power_mw", "MW", 1.0),
        "shiftable_process": ("power_mw", "MW", 18.0),
        "deferrable_aux": ("power_mw", "MW", 9.0),
        "price": ("price_usd_per_mwh", "USD_per_MWh", 70.0),
        "weather_context": ("power_mw", "MW", 0.0),
    }
    result: dict[str, TelemetryRecord] = {}
    for signal_name, (signal, unit, value) in signal_specs.items():
        observed_at = now
        quality = "fresh"
        record_value: float | None = value
        if signal_name == "deferrable_aux" and any(event.event_type == "stale_telemetry" and event.start_slot <= slot <= event.end_slot and "deferrable_aux" in event.affected_ids for event in events):
            observed_at = now - dt.timedelta(hours=2)
        if any(event.event_type == "missing_telemetry" and event.start_slot <= slot <= event.end_slot and signal_name in event.affected_ids for event in events):
            quality = "missing"
            record_value = None
        if any(event.event_type == "stale_telemetry" and event.start_slot <= slot <= event.end_slot and signal_name in event.affected_ids for event in events):
            observed_at = now - dt.timedelta(hours=2)
        if any(event.event_type == "quality" and event.start_slot <= slot <= event.end_slot and signal_name in event.affected_ids for event in events):
            quality = "conflict"
        result[signal_name] = TelemetryRecord(
            telemetry_id=f"{replay_id}:telemetry:{slot}:{signal_name}",
            asset_id=signal_name,
            signal=signal,
            value=record_value,
            unit=unit,
            observed_at=observed_at,
            received_at=now,
            quality=quality,
            provenance=provenance,
        )
    return result


def _quality_report(portfolio: PortfolioFixture, replay_id: str, slot: int, events: list[EventRecord]) -> QualityGateReport:
    signal_records = _synthetic_telemetry(portfolio, replay_id=replay_id, slot=slot, events=events)
    result = evaluate_quality(
        portfolio,
        replay_id=replay_id,
        slot_index=slot,
        signal_records=signal_records,
        signals=list(signal_records),
    )
    return QualityGateReport(
        replay_id=replay_id,
        slot_index=slot,
        decisions=result.decisions,
        triage=result.triage,
        blocks_action=result.blocks_action,
        use_fallback=result.use_fallback,
        provenance=Provenance(label="derived", source_id="quality-gate", source_version="quality-table-v1", input_refs=[item.record_id or item.signal for item in result.decisions]),
    )


def _dr_allowed(report: QualityGateReport, portfolio: PortfolioFixture) -> set[str]:
    allowed = {asset.asset_id for asset in portfolio.assets_of("load_class") if asset.opt_in and not asset.opt_out and not asset.critical}
    for decision in report.decisions:
        if decision.signal in allowed and decision.quality != "fresh":
            allowed.discard(decision.signal)
    return allowed


def _approval_event(execution_replay_id: str, slot: int, proposal: ActionProposal, energy: dict[str, float], flow: float) -> ApprovalEvent:
    return ApprovalEvent(
        replay_id=execution_replay_id,
        decision_id=proposal.decision_id,
        slot_index=slot,
        actor_id="batch-replay-synthetic",
        reason="deterministic batch-replay synthetic approval for simulation replay; no live application",
        synthetic_approval=True,
        proposal_digest=sha256_hex(proposal),
        current_state_digest=_state_digest_for_approval(execution_replay_id, slot, energy, flow),
        provenance=Provenance(label="scenario_assumption", source_id="batch-replay-synthetic-approval", source_version="approval-event-v1", input_refs=[]),
    )


def _approval_audit_event(approval: ApprovalEvent) -> EventRecord:
    return EventRecord(
        event_id=f"event-approval-applied-{approval.slot_index:02d}",
        replay_id=approval.replay_id,
        slot_index=approval.slot_index,
        event_type="approval",
        start_slot=approval.slot_index,
        end_slot=approval.slot_index,
        affected_ids=[approval.actor_id],
        details={"actor": approval.actor_id, "reason": approval.reason, "decision_id": approval.decision_id, "proposal_digest": approval.proposal_digest, "state_digest": approval.current_state_digest, "state": approval.state},
        provenance=approval.provenance,
    )


@dataclass
class ReplayLoopResult:
    twin: TwinResult
    proposals: dict[int, ActionProposal]
    decisions: dict[int, SafetyDecision]
    approvals: dict[int, ApprovalEvent]
    quality_reports: dict[int, QualityGateReport]
    events: list[EventRecord]
    fallback_count: int
    final_energy_mwh: dict[str, float]
    final_flow_mw: float


def replay_twin(
    portfolio: PortfolioFixture,
    scenarios: Any,
    events: list[EventRecord],
    *,
    replay_id: str,
    policy_mode: str,
    carbon_factor: CarbonFactorAssumption,
    deadline: float,
    demo_overrides: bool = False,
) -> ReplayLoopResult:
    """Run the real agentic per-slot replay loop and roll it into a TwinResult.

    Takes the FULL five-path ``ScenarioSet`` and passes
    ``scenario_paths=scenarios.paths`` to ``plan_next`` so the scenario-robust
    MILP is driven exactly as production does (a single realized path would
    silently collapse the robust solve). The two demo-only hacks (slot-42 forced
    policy, slot-41 hand-authored dispatch) fire ONLY when ``demo_overrides`` is
    True; the Monte Carlo loop passes False so each MC day is a clean,
    optimizer-only seeded day with no hand-authored operator-facing number.
    """
    path = next(item for item in scenarios.paths if item.scenario_id == scenarios.realized_path_id)
    quality_reports: dict[int, QualityGateReport] = {}
    proposals: dict[int, ActionProposal] = {}
    decisions: dict[int, SafetyDecision] = {}
    approvals: dict[int, ApprovalEvent] = {}
    energy_state = {asset.asset_id: asset.initial_energy_mwh for asset in portfolio.assets if asset.kind == "bess"}
    previous_flow = portfolio.initial_flow_mw
    fallback_count = 0
    for slot in range(portfolio.slot_count):
        report = _quality_report(portfolio, replay_id, slot, events)
        quality_reports[slot] = report
        remaining = deadline - time.monotonic()
        force_fallback = report.use_fallback or remaining <= FINALIZATION_BUDGET_SECONDS
        selected_policy = "reliability_first" if (demo_overrides and slot == 42) else policy_mode
        allowed_dr = _dr_allowed(report, portfolio)
        result: PlanResult = plan_next(
            portfolio,
            path,
            events,
            replay_id=replay_id,
            slot_index=slot,
            policy_mode=selected_policy,
            energy_state=energy_state,
            previous_flow_mw=previous_flow,
            deadline=deadline,
            request_budget_seconds=min(8.0, max(0.0, remaining)),
            slot_budget_seconds=min(2.0, max(0.0, remaining)),
            force_solver_unavailable=force_fallback,
            forced_fallback_reason=("DATA_QUALITY_BLOCKED" if report.blocks_action else "DATA_QUALITY_FALLBACK") if report.use_fallback else None,
            scenario_paths=scenarios.paths,
            dr_allowed_load_ids=allowed_dr,
            carbon_factor=carbon_factor,
        )
        proposal = result.proposal
        if proposal is None:
            continue
        if demo_overrides and slot == 41:
            proposal = proposal.model_copy(update={"grid_import_mw": [75.0], "grid_export_mw": [0.0], "bess_charge_mw": {**proposal.bess_charge_mw, "bess-a": [0.0]}, "bess_discharge_mw": {**proposal.bess_discharge_mw, "bess-a": [8.0]}})
        decision = validate_proposal(
            portfolio,
            path,
            events,
            proposal,
            replay_id=replay_id,
            slot_index=slot,
            energy_state=energy_state,
            previous_flow_mw=previous_flow,
            quality_decisions=report.decisions,
        )
        proposals[slot] = proposal
        decisions[slot] = decision
        if proposal.solver_result.status != "OPTIMAL":
            fallback_count += 1
        if decision.status == "ACCEPTED":
            approval = _approval_event(replay_id, slot, proposal, energy_state, previous_flow)
            approvals[slot] = approval
            events.append(_approval_audit_event(approval))
            for asset in portfolio.assets:
                if asset.kind == "bess":
                    energy_state[asset.asset_id] = bess_energy_next(energy_state[asset.asset_id], proposal.bess_charge_mw[asset.asset_id][0], proposal.bess_discharge_mw[asset.asset_id][0], asset)
            previous_flow = proposal.grid_import_mw[0] - proposal.grid_export_mw[0]
    twin = run_twin(
        portfolio,
        path,
        events,
        proposals,
        decisions,
        replay_id=replay_id,
        applied_slots=set(approvals),
        approvals=approvals,
        quality_by_slot={slot: report.decisions for slot, report in quality_reports.items()},
        carbon_factor=carbon_factor,
    )
    return ReplayLoopResult(
        twin=twin,
        proposals=proposals,
        decisions=decisions,
        approvals=approvals,
        quality_reports=quality_reports,
        events=events,
        fallback_count=fallback_count,
        final_energy_mwh=twin.final_energy_mwh,
        final_flow_mw=twin.final_flow_mw,
    )


def execute_replay(*, seed: int, policy_mode: str, replay_id: str | None = None, adapter_state: str = "DISABLED", deadline_seconds: float = REPLAY_BUDGET_SECONDS) -> ReplayExecution:
    start = time.monotonic()
    _configure_demo_trust()
    ensure_runtime_dirs()
    replay_id = replay_id or f"replay-{seed}-{policy_mode}"
    carbon_factor = load_carbon_factor()
    adapter = VayuContextAdapter(
        enabled=adapter_state == "VAYU_CONTEXT_ONLY",
        fixture_path=Path(os.environ["MONSOON_GRID_VAYU_CONTEXT_PATH"]) if os.environ.get("MONSOON_GRID_VAYU_CONTEXT_PATH") else None,
        allowlisted_url=os.environ.get("MONSOON_GRID_VAYU_CONTEXT_URL"),
        expected_sha256=os.environ.get("MONSOON_GRID_VAYU_CONTEXT_SHA256"),
        allowlisted_urls=tuple(filter(None, os.environ.get("MONSOON_GRID_VAYU_ALLOWLIST", "").split(","))),
    )
    weather_context = adapter.read()
    portfolio = load_demo_portfolio().model_copy(update={"seed": seed, "fixture_set_hash": fixture_set_hash()})
    events = _event_replay(load_demo_events(), replay_id)
    scenarios = generate_scenarios(portfolio, seed=seed)
    path = next(item for item in scenarios.paths if item.scenario_id == scenarios.realized_path_id)
    forecasts = build_forecasts(portfolio)
    deadline = start + min(float(deadline_seconds), REPLAY_BUDGET_SECONDS)
    loop = replay_twin(
        portfolio,
        scenarios,
        events,
        replay_id=replay_id,
        policy_mode=policy_mode,
        carbon_factor=carbon_factor,
        deadline=deadline,
        demo_overrides=True,
    )
    quality_reports = loop.quality_reports
    proposals = loop.proposals
    decisions = loop.decisions
    approvals = loop.approvals
    events = loop.events
    fallback_count = loop.fallback_count
    twin = loop.twin
    baseline_twin = run_greedy_p50_baseline(portfolio, path, _event_replay(load_demo_events(), f"{replay_id}:baseline"), replay_id=replay_id, carbon_factor=carbon_factor)
    elapsed = time.monotonic() - start
    return ReplayExecution(
        replay_id=replay_id,
        portfolio=portfolio,
        events=events,
        scenarios=scenarios,
        forecasts=forecasts,
        quality_reports=quality_reports,
        proposals=proposals,
        decisions=decisions,
        approvals=approvals,
        twin=twin,
        baseline_twin=baseline_twin,
        elapsed_seconds=elapsed,
        fallback_count=fallback_count,
        adapter_state=adapter.status.state,
        adapter_status=adapter.status,
        weather_context=weather_context,
        carbon_factor=carbon_factor,
    )


def _read_optional_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _certificate_for_decision(execution: ReplayExecution, slot: int, *, store: PersistenceStore, final: bool = False):
    proposal = execution.proposals[slot]
    decision = execution.decisions[slot]
    if decision.status == "ACCEPTED":
        status = "FALLBACK" if proposal.solver_result.status != "OPTIMAL" else "ACCEPTED"
    elif decision.status == "ESCALATED_NO_SAFE_ACTION":
        status = "ESCALATED"
    else:
        status = "REJECTED"
    protected_manifest = _read_optional_json(PROVENANCE_ROOT / "protected_manifest_before.json")
    install_manifest = _read_optional_json(RUNTIME_ROOT / "install-manifest.json")
    fixture_manifest = {name: sha256_hex(_read_optional_json(FIXTURE_ROOT / name)) for name in FIXTURE_SET_FILES}
    quality_payload = {str(index): report for index, report in sorted(execution.quality_reports.items())}
    approval = execution.approvals.get(slot)
    required_evidence = ["portfolio", "events", "scenario_set", "realized_path", "forecasts", "quality_reports", "proposal", "decision", "metrics", "baseline_metrics", "carbon_factor", "solver_configuration", "adapter_evidence", "fixture_manifest", "protected_manifest_before", "install_manifest", "approval_events"]
    evidence = {
        "replay_version": REPLAY_VERSION,
        "replay_id": execution.replay_id,
        "decision_slot": slot,
        "fixture_set_hash": execution.portfolio.fixture_set_hash,
        "replay_clock": {"fixture_start_utc": execution.portfolio.fixture_start_utc, "dt_hours": execution.portfolio.dt_hours, "slot_count": execution.portfolio.slot_count},
        "policy_mode": proposal.policy_mode,
        "policy_objective_sequence": POLICY_OBJECTIVES[proposal.policy_mode],
        "solver_status": proposal.solver_result.status,
        "solver_method_version": proposal.solver_result.method_hash,
        "adapter_state": execution.adapter_state,
        "adapter_provenance": {"source": execution.adapter_status.source, "content_sha256": execution.adapter_status.content_sha256, "reason": execution.adapter_status.reason, "weather_context": execution.weather_context},
        "carbon_factor": execution.carbon_factor,
        "required_evidence": required_evidence,
        "offline_replay": True,
        "bess_numeric_tolerance_mwh": 0.000001,
        "runtime_metadata_excluded_from_digest": True,
    }
    objects = {
        "portfolio": execution.portfolio,
        "events": execution.events,
        "scenario_set": execution.scenarios,
        "realized_path": path_for_execution(execution),
        "forecasts": execution.forecasts,
        "quality_reports": quality_payload,
        "proposal": proposal,
        "decision": decision,
        "approval_events": {str(slot): approval for slot, approval in sorted(execution.approvals.items())},
        "metrics": execution.twin.metrics,
        "baseline_metrics": execution.baseline_twin.metrics,
        "carbon_factor": execution.carbon_factor,
        "solver_configuration": {"method_version": "scenario-milp-lexicographic-v2", "objective_sequence": POLICY_OBJECTIVES[proposal.policy_mode], "dt_hours": 0.25, "numeric_tolerance_mwh": 0.000001},
        "adapter_evidence": evidence["adapter_provenance"],
        "fixture_manifest": fixture_manifest,
        "protected_manifest_before": protected_manifest,
        "install_manifest": install_manifest,
    }
    certificate = create_certificate(replay_id=execution.replay_id, decision_id=decision.decision_id, decision_status=status, evidence=evidence, objects=objects, settings=Settings.from_env(), store=store)
    target = ARTIFACT_ROOT / "certificate.json" if final else ARTIFACT_ROOT / "decisions" / f"slot-{slot:02d}.json"
    export_certificate(certificate, path=target, store=store)
    return certificate, target


def path_for_execution(execution: ReplayExecution):
    return next(item for item in execution.scenarios.paths if item.scenario_id == execution.scenarios.realized_path_id)


def write_execution_artifacts(execution: ReplayExecution) -> Path:
    store = PersistenceStore()
    decision_certificates: list[str] = []
    final_slot = 42 if 42 in execution.decisions else max(execution.decisions)
    for slot in sorted(execution.decisions):
        _, target = _certificate_for_decision(execution, slot, store=store, final=slot == final_slot)
        decision_certificates.append(str(target.relative_to(RUNTIME_ROOT)).replace("\\", "/"))
    replay_payload = {
        "replay_id": execution.replay_id,
        "seed": execution.scenarios.seed,
        "policy_mode": execution.proposals.get(0).policy_mode if execution.proposals else "balanced",
        "fixture_set_hash": execution.portfolio.fixture_set_hash,
        "scenario_set": execution.scenarios,
        "realized_path_id": execution.scenarios.realized_path_id,
        "realized_path_sha256": execution.scenarios.realized_path_sha256,
        "baseline_method": "greedy-p50-baseline-v2",
        "baseline_realized_path_id": execution.scenarios.realized_path_id,
        "baseline_realized_path_sha256": execution.scenarios.realized_path_sha256,
        "baseline_metrics": execution.baseline_twin.metrics,
        "slot_states": execution.twin.slot_states,
        "quality_reports": {str(slot): report for slot, report in sorted(execution.quality_reports.items())},
        "approvals": {str(slot): approval for slot, approval in sorted(execution.approvals.items())},
        "decisions": {str(slot): decision for slot, decision in sorted(execution.decisions.items())},
        "planned_fallback_count": execution.fallback_count,
        "adapter_state": execution.adapter_state,
        "carbon_factor": execution.carbon_factor,
        "decision_certificates": decision_certificates,
    }
    _write_canonical(ARTIFACT_ROOT / "replay-results.json", replay_payload)
    _write_canonical(ARTIFACT_ROOT / "metrics.json", execution.twin.metrics)
    _write_canonical(ARTIFACT_ROOT / "baseline-metrics.json", execution.baseline_twin.metrics)
    _write_canonical(ARTIFACT_ROOT / "quality-reports.json", {str(slot): report for slot, report in sorted(execution.quality_reports.items())})
    _write_canonical(ARTIFACT_ROOT / "approval-events.json", {str(slot): approval for slot, approval in sorted(execution.approvals.items())})
    trace_path = ARTIFACT_ROOT / "event-trace.jsonl"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_bytes(b"".join(canonical_bytes(state) + b"\n" for state in execution.twin.slot_states))
    summary = DemoSummary(
        replay_id=execution.replay_id,
        baseline_metrics=execution.baseline_twin.metrics,
        slot_count=len(execution.twin.slot_states),
        policy_mode=execution.proposals.get(0).policy_mode if execution.proposals else "balanced",
        realized_path_id=execution.scenarios.realized_path_id,
        realized_path_sha256=execution.scenarios.realized_path_sha256,
        planner_status="OPTIMAL" if all(proposal.solver_result.status == "OPTIMAL" for proposal in execution.proposals.values()) else "EXPLICIT_FALLBACK_USED",
        planned_fallback_count=execution.fallback_count,
        unsafe_rejections=sum(1 for decision in execution.decisions.values() if decision.status != "ACCEPTED"),
        final_safety_status=execution.decisions.get(final_slot).status if final_slot in execution.decisions else "UNAVAILABLE",
        adapter_state=execution.adapter_state,
        metrics=execution.twin.metrics,
        certificate_path="artifacts/certificate.json",
        provenance=Provenance(label="derived", source_id="demo", source_version=REPLAY_VERSION, input_refs=[execution.replay_id]),
    )
    _write_canonical(ARTIFACT_ROOT / "demo-summary.json", summary)
    _write_latency_report(execution)
    return ARTIFACT_ROOT / "demo-summary.json"


def _write_latency_report(execution: ReplayExecution) -> None:
    """Write runtime/artifacts/latency.json (A6): measured, NON-canonical, excluded from every digest."""
    from .contracts import LatencyReport, LatencySample
    from .metrics import build_metric

    samples = list(getattr(execution, "latency_samples", []) or [])
    totals = sorted(s.elapsed_seconds for s in samples if s.phase == "total")
    if not totals:
        # One measured total sample from the replay wall time so the report is real.
        totals = [round(execution.elapsed_seconds, 6)]
        samples = [LatencySample(replay_id=execution.replay_id, slot_index=0, phase="total", stage_name=None, elapsed_seconds=round(execution.elapsed_seconds, 6), budget_seconds=round(REPLAY_BUDGET_SECONDS, 6), remaining_seconds=round(max(0.0, REPLAY_BUDGET_SECONDS - execution.elapsed_seconds), 6))]

    def _pct(q: float) -> float:
        index = min(len(totals) - 1, max(0, math.ceil(q * len(totals)) - 1))
        return round(totals[index], 6)

    report = LatencyReport(
        replay_id=execution.replay_id,
        samples=samples,
        values=[
            build_metric("planner_latency_p50_seconds", _pct(0.50)),
            build_metric("planner_latency_p95_seconds", _pct(0.95)),
            build_metric("replay_wall_seconds", round(execution.elapsed_seconds, 6)),
        ],
    )
    payload = json.loads(report.model_dump_json())
    (ARTIFACT_ROOT / "latency.json").write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")


def cmd_inventory(args: argparse.Namespace) -> int:
    result = inventory(Path(args.output), compare_to=Path(args.compare_to) if args.compare_to else None)
    print(json.dumps({"output": args.output, "protected_path_count": len(result["protected_paths"]), "comparison": result["comparison"]}, sort_keys=True))
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    execution = execute_replay(seed=args.seed, policy_mode=args.policy, deadline_seconds=args.deadline)
    write_execution_artifacts(execution)
    print(json.dumps({"replay_id": execution.replay_id, "slots": len(execution.twin.slot_states), "realized_path_id": execution.scenarios.realized_path_id, "realized_path_sha256": execution.scenarios.realized_path_sha256, "elapsed_seconds": round(execution.elapsed_seconds, 6), "planned_fallback_count": execution.fallback_count, "adapter_state": execution.adapter_state, "certificate": str(ARTIFACT_ROOT / "certificate.json")}, sort_keys=True))
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    start = time.monotonic()
    execution = execute_replay(seed=args.seed, policy_mode=args.policy, adapter_state=args.adapter_state, deadline_seconds=min(args.deadline, REPLAY_BUDGET_SECONDS))
    summary_path = write_execution_artifacts(execution)
    elapsed = time.monotonic() - start
    print("MonsoonGrid DEMO — SIMULATION / REPLAY / NO LIVE CONTROL / NO MARKET SUBMISSION")
    print(f"replay_id={execution.replay_id} slots={len(execution.twin.slot_states)} scenario={execution.scenarios.realized_path_id}")
    print(f"elapsed_seconds={elapsed:.6f} planned_fallback_count={execution.fallback_count} unsafe_rejections={sum(1 for decision in execution.decisions.values() if decision.status != 'ACCEPTED')} approval_events={len(execution.approvals)} adapter_state={execution.adapter_state}")
    print(f"certificate={ARTIFACT_ROOT / 'certificate.json'} summary={summary_path}")
    return 0 if elapsed < 300.0 else 1


def cmd_verify(args: argparse.Namespace) -> int:
    result = load_and_verify(Path(args.certificate_path), settings=Settings.from_env(), store=PersistenceStore())
    print(json.dumps(result, sort_keys=True))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn
    uvicorn.run("deploy.asgi:app", app_dir=str(PRODUCT_ROOT), host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_ingest_weather(args: argparse.Namespace) -> int:
    import datetime as _dt

    from .canonical import sha256_hex
    from .config import RUNTIME_ROOT
    from .ingest import NasaPowerSource, OpenMeteoSource, ResponseCache, WeatherRequest, fetch

    variables = tuple(sorted(set(v.strip() for v in args.variables.split(",") if v.strip())))
    request = WeatherRequest(
        site_id=args.site_id,
        latitude=args.latitude,
        longitude=args.longitude,
        start_date=_dt.date.fromisoformat(args.start_date),
        end_date=_dt.date.fromisoformat(args.end_date),
        variables=variables,
        issue_date=_dt.date.fromisoformat(args.issue_date) if args.issue_date else None,
    )
    source = OpenMeteoSource() if args.source == "openmeteo" else NasaPowerSource()
    cache_root = Path(args.cache_root) if args.cache_root else (RUNTIME_ROOT / "cache")
    cache = ResponseCache(cache_root)
    url = source.request_url(request)
    cache_key = sha256_hex({"source_id": source.source_id, "url": url, "request": request.model_dump(mode="python")})
    records = fetch(source, request, mode=args.mode, cache=cache)
    content_sha256 = records[0].response_content_sha256 if records else ""
    retrieved_at = records[0].retrieved_at_utc.isoformat().replace("+00:00", "Z") if records else ""
    print(json.dumps({
        "cache_key": cache_key,
        "cache_path": str(cache._path(source.source_id, cache_key)),
        "source_url": url,
        "retrieved_at_utc": retrieved_at,
        "response_content_sha256": content_sha256,
        "record_count": len(records),
        "mode": args.mode,
    }, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="monsoon_grid")
    sub = parser.add_subparsers(dest="command", required=True)
    inventory_parser = sub.add_parser("inventory")
    inventory_parser.add_argument("--output", required=True)
    inventory_parser.add_argument("--compare-to", default="")
    inventory_parser.set_defaults(function=cmd_inventory)
    replay_parser = sub.add_parser("replay")
    replay_parser.add_argument("--seed", type=int, default=20260701)
    replay_parser.add_argument("--policy", choices=sorted(POLICY_OBJECTIVES), default="balanced")
    replay_parser.add_argument("--deadline", type=float, default=REPLAY_BUDGET_SECONDS)
    replay_parser.set_defaults(function=cmd_replay)
    demo_parser = sub.add_parser("demo")
    demo_parser.add_argument("--seed", type=int, default=20260701)
    demo_parser.add_argument("--policy", choices=sorted(POLICY_OBJECTIVES), default="balanced")
    demo_parser.add_argument("--deadline", type=float, default=REPLAY_BUDGET_SECONDS)
    demo_parser.add_argument("--adapter-state", choices=["DISABLED", "VAYU_CONTEXT_ONLY"], default="DISABLED")
    demo_parser.set_defaults(function=cmd_demo)
    verify_parser = sub.add_parser("verify-certificate")
    verify_parser.add_argument("--certificate-path", default=str(ARTIFACT_ROOT / "certificate.json"))
    verify_parser.set_defaults(function=cmd_verify)
    serve_parser = sub.add_parser("serve")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.set_defaults(function=cmd_serve)
    ingest_parser = sub.add_parser("ingest-weather")
    ingest_parser.add_argument("--source", choices=["openmeteo", "nasa-power"], required=True)
    ingest_parser.add_argument("--site-id", required=True)
    ingest_parser.add_argument("--latitude", type=float, required=True)
    ingest_parser.add_argument("--longitude", type=float, required=True)
    ingest_parser.add_argument("--start-date", required=True)
    ingest_parser.add_argument("--end-date", required=True)
    ingest_parser.add_argument("--issue-date", default="")
    ingest_parser.add_argument("--variables", required=True)
    ingest_parser.add_argument("--mode", choices=["offline", "cached_first", "live"], default="offline")
    ingest_parser.add_argument("--cache-root", default="")
    ingest_parser.set_defaults(function=cmd_ingest_weather)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.function(args))
    except Exception as exc:
        print(json.dumps({"code": getattr(exc, "code", "INTERNAL_ERROR"), "message": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
