"""Local FastAPI service with typed replay-only semantics."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .canonical import canonical_text, sha256_hex, state_digest
from .certificates import ensure_demo_trust
from .cli import execute_replay, write_execution_artifacts
from .config import ARTIFACT_ROOT, PRODUCT_ROOT, REPLAY_VERSION
from .contracts import RESERVED_ACTOR_IDS, ApprovalEvent, ApprovalRecord, ApprovalRequest, DemoRequest, PlanningRequest, Provenance, ReplayAdvanceRequest, ReplayCreateRequest, ValidationRequest
from .forecasts import build_forecasts
from .fixtures import fixture_set_hash, load_demo_events, load_demo_portfolio
from .cli import _approval_audit_event
from .persistence import PersistenceStore
from .planner import plan_next
from .validator import validate_proposal

app = FastAPI(title="MonsoonGrid P4", version=REPLAY_VERSION, description="Local SIMULATION/REPLAY service; no live control or market submission.")


@dataclass
class ReplayRecord:
    execution: Any
    slot_index: int = 0
    approval_state: str = "PENDING"
    state_digest: str = ""
    approval_events: dict[int, ApprovalEvent] | None = None


REPLAYS: dict[str, ReplayRecord] = {}
IDEMPOTENCY: dict[str, tuple[str, Any]] = {}
LAST_ADAPTER_STATUS: dict[str, Any] = {"state": "DISABLED", "source": "none", "content_sha256": None, "reason": "VAYU context adapter disabled by default", "scope": "weather-context-only", "no_authoritative_fields": True}
STORE = PersistenceStore()


def register_execution(execution: Any) -> str:
    """Seed in-memory read state from an already-computed execution.

    Used by the gate script and tests to expose a replay's metrics through the
    read endpoints without running a second replay. Adds no control path.
    """
    record = ReplayRecord(execution=execution, slot_index=0)
    record.state_digest = _state_digest(record)
    REPLAYS[execution.replay_id] = record
    return execution.replay_id


def _envelope(*, status: str, data: Any = None, error: Any = None, request_id: str | None = None) -> dict[str, Any]:
    return {"schema_version": "1.0", "request_id": request_id or uuid.uuid4().hex, "status": status, "data": data, "error": error, "replay_scope": "REPLAY"}


def _error(code: str, message: str, *, replay_id: str | None = None, retryable: bool = False, recoverable: bool = False) -> dict[str, Any]:
    return {"code": code, "message": message, "field_errors": {}, "replay_id": replay_id, "slot_index": None, "recoverable": recoverable, "retryable": retryable}


def _idempotency(header: str | None, body_key: str, payload: Any) -> tuple[str, Any] | None:
    """Persisted idempotency over the store, with an in-process cache (A5)."""
    if header is None or header != body_key:
        raise HTTPException(status_code=422, detail={"code": "IDEMPOTENCY_KEY_MISMATCH", "message": "Idempotency-Key header must equal body idempotency_key"})
    digest = sha256_hex(payload)
    cached = IDEMPOTENCY.get(header)
    if cached and cached[0] == digest:
        return cached
    stored = STORE.get_idempotency(header)
    if stored is not None:
        if stored[0] != digest:
            raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_CONFLICT", "message": "idempotency key was already used for a different request"})
        IDEMPOTENCY[header] = (stored[0], stored[1])
        return IDEMPOTENCY[header]
    if cached and cached[0] != digest:
        raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_CONFLICT", "message": "idempotency key was already used for a different request"})
    return None


def _remember(key: str, payload: Any, response: Any) -> None:
    digest = sha256_hex(payload)
    STORE.remember_idempotency(key, digest, json.loads(canonical_text(response)))
    IDEMPOTENCY[key] = (digest, response)


def _state_digest(record: ReplayRecord) -> str:
    execution = record.execution
    record.state_digest = state_digest(replay_id=record.execution.replay_id, slot_index=record.slot_index, portfolio_state=execution.portfolio, twin_state=execution.twin.slot_states[record.slot_index - 1] if record.slot_index else {}, pending_approval_state=record.approval_state, applied_event_ids=[event.event_id for event in execution.events if event.start_slot < record.slot_index])
    return record.state_digest


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "scope": "SIMULATION", "replay_scope": "REPLAY", "labels": ["SIMULATION", "REPLAY", "NO LIVE CONTROL / NO MARKET SUBMISSION"]}


@app.get("/ready")
def ready() -> dict[str, Any]:
    try:
        ensure_demo_trust()
        return {"status": "ready", "certificate_signing": "Ed25519", "solver": "scipy.optimize.milp/HiGHS-or-fallback"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=_error("DEPENDENCY_UNAVAILABLE", str(exc), retryable=True)) from exc


@app.post("/replays")
def create_replay(request: ReplayCreateRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    previous = _idempotency(idempotency_key, request.idempotency_key, request.model_dump(mode="python"))
    if previous:
        return previous[1]
    expected = fixture_set_hash()
    if request.fixture_set_hash != expected:
        response = _envelope(status="error", error=_error("REPLAY_VERSION_MISMATCH", "fixture set hash does not match checked-in fixture", retryable=False))
        _remember(request.idempotency_key, request.model_dump(mode="python"), response)
        return JSONResponse(status_code=422, content=response)
    execution = execute_replay(seed=request.seed, policy_mode=request.policy_mode, adapter_state=request.adapter_state)
    LAST_ADAPTER_STATUS.update({"state": execution.adapter_state, "source": execution.adapter_status.source, "content_sha256": execution.adapter_status.content_sha256, "reason": execution.adapter_status.reason})
    record = ReplayRecord(execution=execution, state_digest="", approval_events={})
    REPLAYS[execution.replay_id] = record
    digest = _state_digest(record)
    data = {"replay_id": execution.replay_id, "slot_index": 0, "state_digest": digest, "fixture_set_hash": expected, "adapter_state": execution.adapter_state}
    response = _envelope(status="ok", data=data)
    _remember(request.idempotency_key, request.model_dump(mode="python"), response)
    return response


@app.get("/replays/{replay_id}")
def get_replay(replay_id: str):
    record = REPLAYS.get(replay_id)
    if not record:
        raise HTTPException(status_code=404, detail=_error("INPUT_MISSING", "replay is not present", replay_id=replay_id))
    return _envelope(status="ok", data={"replay_id": replay_id, "slot_index": record.slot_index, "state_digest": _state_digest(record), "approval_state": record.approval_state})


@app.post("/replays/{replay_id}/advance")
def advance_replay(replay_id: str, request: ReplayAdvanceRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    previous = _idempotency(idempotency_key, request.idempotency_key, request.model_dump(mode="python"))
    if previous:
        return previous[1]
    record = REPLAYS.get(replay_id)
    if not record or request.replay_id != replay_id:
        raise HTTPException(status_code=409, detail=_error("STATE_CONFLICT", "replay identity does not match", replay_id=replay_id, retryable=True))
    current_digest = _state_digest(record)
    if request.expected_slot_index != record.slot_index or request.expected_state_digest != current_digest:
        raise HTTPException(status_code=409, detail=_error("STATE_CONFLICT", "expected slot or state digest is stale", replay_id=replay_id, retryable=True))
    if request.proposal is not None:
        path = next(item for item in record.execution.scenarios.paths if item.scenario_id == record.execution.scenarios.realized_path_id)
        decision = validate_proposal(record.execution.portfolio, path, record.execution.events, request.proposal, replay_id=replay_id, slot_index=record.slot_index)
        proposal = request.proposal
    else:
        path = next(item for item in record.execution.scenarios.paths if item.scenario_id == record.execution.scenarios.realized_path_id)
        result = plan_next(record.execution.portfolio, path, record.execution.events, replay_id=replay_id, slot_index=record.slot_index, policy_mode="balanced", scenario_paths=record.execution.scenarios.paths)
        proposal = result.proposal
        decision = validate_proposal(record.execution.portfolio, path, record.execution.events, proposal, replay_id=replay_id, slot_index=record.slot_index)
    if proposal is None:
        raise HTTPException(status_code=503, detail=_error("DEPENDENCY_UNAVAILABLE", "planner returned no proposal", replay_id=replay_id, retryable=True))
    record.execution.decisions[record.slot_index] = decision
    record.execution.proposals[record.slot_index] = proposal
    approval = (record.approval_events or {}).get(record.slot_index)
    # A5: a mutation requires a PERSISTED approval, not just an in-memory one.
    persisted = STORE.load_approval(replay_id, decision.decision_id) is not None
    token_matches = approval is not None and persisted and request.approval_token in {approval.record_id, approval.proposal_digest}
    if decision.status == "ACCEPTED" and not token_matches:
        record.approval_state = "PENDING"
        data = {"replay_id": replay_id, "slot_index": record.slot_index, "state_digest": _state_digest(record), "decision": decision, "approval_required": True}
        response = _envelope(status="ok", data=data)
    elif decision.status == "ACCEPTED" and token_matches:
        record.approval_state = "APPLIED"
        record.slot_index = min(96, record.slot_index + 1)
        data = {"replay_id": replay_id, "slot_index": record.slot_index, "state_digest": _state_digest(record), "decision": decision, "approval_event": approval}
        response = _envelope(status="ok", data=data)
    else:
        data = {"replay_id": replay_id, "slot_index": record.slot_index, "state_digest": _state_digest(record), "decision": decision, "approval_required": False}
        response = _envelope(status="rejected", data=data)
    _remember(request.idempotency_key, request.model_dump(mode="python"), response)
    return response


@app.post("/validate")
def validate(request: ValidationRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    previous = _idempotency(idempotency_key, request.idempotency_key, request.model_dump(mode="python"))
    if previous:
        return previous[1]
    record = REPLAYS.get(request.replay_id)
    if record is None:
        raise HTTPException(status_code=409, detail=_error("STATE_CONFLICT", "replay is not present", replay_id=request.replay_id, retryable=True))
    if request.expected_state_digest != _state_digest(record):
        raise HTTPException(status_code=409, detail=_error("STATE_CONFLICT", "state digest is stale", replay_id=request.replay_id, retryable=True))
    path = next(item for item in record.execution.scenarios.paths if item.scenario_id == record.execution.scenarios.realized_path_id)
    decision = validate_proposal(record.execution.portfolio, path, record.execution.events, request.proposal, replay_id=request.replay_id, slot_index=record.slot_index)
    response = _envelope(status="ok" if decision.status == "ACCEPTED" else "rejected", data=decision)
    _remember(request.idempotency_key, request.model_dump(mode="python"), response)
    return response


@app.post("/approvals")
def approval(request: ApprovalRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    previous = _idempotency(idempotency_key, request.idempotency_key, request.model_dump(mode="python"))
    if previous:
        return previous[1]
    record = REPLAYS.get(request.replay_id)
    if record is None or request.expected_state_digest != _state_digest(record):
        raise HTTPException(status_code=409, detail=_error("STATE_CONFLICT", "replay or expected digest is stale", replay_id=request.replay_id, retryable=True))
    if request.actor_id in RESERVED_ACTOR_IDS:
        raise HTTPException(status_code=422, detail=_error("SYNTHETIC_ACTOR_FORBIDDEN", "the batch-replay synthetic actor cannot approve through the interactive API", replay_id=request.replay_id))
    if request.action in {"approve", "override"} and (request.reason is None or not request.reason.strip()):
        raise HTTPException(status_code=422, detail=_error("APPROVAL_REASON_REQUIRED", "an approval reason is required", replay_id=request.replay_id))
    decision = next((candidate for candidate in record.execution.decisions.values() if candidate.decision_id == request.decision_id), None)
    proposal = next((candidate for candidate in record.execution.proposals.values() if candidate.decision_id == request.decision_id), None)
    if request.action in {"approve", "override"} and (decision is None or proposal is None or decision.status != "ACCEPTED" or any(item.severity == "fatal" for item in decision.violations)):
        response = _envelope(status="rejected", data={"state": "REJECTED", "reason": "fatal or non-accepted validator decision cannot be approved or overridden"})
    elif request.action in {"approve", "override"}:
        approval_event = ApprovalEvent(
            replay_id=request.replay_id,
            decision_id=request.decision_id,
            slot_index=decision.slot_index,
            actor_id=request.actor_id,
            reason=request.reason or "operator approval",
            synthetic_approval=False,
            proposal_digest=sha256_hex(proposal),
            current_state_digest=request.expected_state_digest,
            provenance=Provenance(label="derived", source_id="api-approval", source_version="approval-event-v1", input_refs=[proposal.record_id or proposal.proposal_id]),
        )
        approval_record = ApprovalRecord(
            replay_id=request.replay_id,
            decision_id=request.decision_id,
            actor_id=request.actor_id,
            state="APPROVED",
            action=request.action,
            reason=request.reason,
            expected_state_digest=request.expected_state_digest,
            approval_digest=sha256_hex(approval_event),
            provenance=Provenance(label="derived", source_id="api-approval", source_version="approval-record-v1", input_refs=[approval_event.record_id or request.decision_id]),
        )
        audit_event = _approval_audit_event(approval_event)
        try:
            STORE.record_approval_and_events(
                approval={**approval_record.model_dump(mode="python"), "slot_index": approval_event.slot_index, "synthetic_approval": False},
                events=[audit_event.model_dump(mode="python")],
            )
        except Exception as exc:  # noqa: BLE001 - persistence failure maps to 503
            raise HTTPException(status_code=503, detail=_error("DEPENDENCY_UNAVAILABLE", f"approval persistence failed: {exc}", replay_id=request.replay_id, retryable=True)) from exc
        if record.approval_events is None:
            record.approval_events = {}
        record.approval_events[decision.slot_index] = approval_event
        record.approval_state = "APPROVED"
        response = _envelope(status="ok", data={"state": record.approval_state, "actor_id": request.actor_id, "reason": request.reason, "approval_event": approval_event})
    else:
        record.approval_state = "ESCALATED" if request.action == "escalate" else "REJECTED"
        response = _envelope(status="ok", data={"state": record.approval_state, "actor_id": request.actor_id, "reason": request.reason})
    _remember(request.idempotency_key, request.model_dump(mode="python"), response)
    return response


@app.get("/portfolio")
def portfolio():
    return _envelope(status="ok", data=load_demo_portfolio())


@app.get("/state/{replay_id}")
def state(replay_id: str):
    record = REPLAYS.get(replay_id)
    if not record:
        raise HTTPException(status_code=404, detail=_error("INPUT_MISSING", "replay is not present", replay_id=replay_id))
    return _envelope(status="ok", data={"replay_id": replay_id, "slot_index": record.slot_index, "state_digest": _state_digest(record), "bess_energy_mwh": record.execution.twin.slot_states[max(0, record.slot_index - 1)].bess_energy_mwh if record.slot_index else {asset.asset_id: asset.initial_energy_mwh for asset in record.execution.portfolio.assets if asset.kind == "bess"}})


@app.get("/forecasts/{replay_id}")
def forecasts(replay_id: str):
    record = REPLAYS.get(replay_id)
    if not record:
        raise HTTPException(status_code=404, detail=_error("INPUT_MISSING", "replay is not present", replay_id=replay_id))
    return _envelope(status="ok", data=build_forecasts(record.execution.portfolio))


@app.get("/scenarios/{replay_id}")
def scenarios(replay_id: str):
    record = REPLAYS.get(replay_id)
    if not record:
        raise HTTPException(status_code=404, detail=_error("INPUT_MISSING", "replay is not present", replay_id=replay_id))
    return _envelope(status="ok", data=record.execution.scenarios)


@app.post("/planning")
def planning(request: PlanningRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    previous = _idempotency(idempotency_key, request.idempotency_key, request.model_dump(mode="python"))
    if previous:
        return previous[1]
    record = REPLAYS.get(request.replay_id)
    if record is None or request.expected_state_digest != _state_digest(record) or request.slot_index != record.slot_index:
        raise HTTPException(status_code=409, detail=_error("STATE_CONFLICT", "planning state is stale", replay_id=request.replay_id, retryable=True))
    path = next(item for item in record.execution.scenarios.paths if item.scenario_id == record.execution.scenarios.realized_path_id)
    result = plan_next(record.execution.portfolio, path, record.execution.events, replay_id=request.replay_id, slot_index=request.slot_index, policy_mode=request.policy_mode)
    response = _envelope(status="ok", data=result)
    _remember(request.idempotency_key, request.model_dump(mode="python"), response)
    return response


@app.get("/metrics/{replay_id}")
def metrics(replay_id: str):
    record = REPLAYS.get(replay_id)
    if not record:
        raise HTTPException(status_code=404, detail=_error("INPUT_MISSING", "replay is not present", replay_id=replay_id))
    return _envelope(status="ok", data=record.execution.twin.metrics)


@app.get("/events/{replay_id}")
def events(replay_id: str):
    record = REPLAYS.get(replay_id)
    if not record:
        raise HTTPException(status_code=404, detail=_error("INPUT_MISSING", "replay is not present", replay_id=replay_id))
    return _envelope(status="ok", data=record.execution.events)


@app.get("/latency/{replay_id}")
def latency(replay_id: str):
    # Measured, non-deterministic latency report (A6). Read from the non-canonical
    # latency.json artifact when present, else synthesize from the in-memory execution.
    path = ARTIFACT_ROOT / "latency.json"
    if path.exists():
        try:
            return _envelope(status="ok", data=json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    record = REPLAYS.get(replay_id)
    if not record:
        raise HTTPException(status_code=404, detail=_error("INPUT_MISSING", "replay is not present", replay_id=replay_id))
    return _envelope(status="ok", data={"replay_id": replay_id, "samples": [], "values": []})


@app.get("/solver/{replay_id}")
def solver_status(replay_id: str, slot: int = 0):
    record = REPLAYS.get(replay_id)
    if not record:
        raise HTTPException(status_code=404, detail=_error("INPUT_MISSING", "replay is not present", replay_id=replay_id))
    if slot < 0 or slot > 95:
        raise HTTPException(status_code=422, detail=_error("INPUT_INVALID", "slot must be in [0, 95]", replay_id=replay_id))
    proposal = record.execution.proposals.get(slot)
    decision = record.execution.decisions.get(slot)
    if proposal is None:
        raise HTTPException(status_code=404, detail=_error("INPUT_MISSING", "no proposal for slot", replay_id=replay_id))
    violations = decision.violations if decision else []
    return _envelope(status="ok", data={
        "replay_id": replay_id,
        "slot_index": slot,
        "solver_name": proposal.solver_result.solver_name,
        "solver_version": proposal.solver_result.solver_version,
        "status": proposal.solver_result.status,
        "fallback_reason": proposal.solver_result.fallback_reason,
        "violation_count": len(violations),
        "fatal_violation_count": sum(1 for v in violations if v.severity == "fatal"),
    })


@app.get("/certificates/{certificate_id}/verification")
def certificate_verification(certificate_id: str):
    from .certificates import load_and_verify

    candidates = list(ARTIFACT_ROOT.glob("**/*.json"))
    for candidate in candidates:
        try:
            raw = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if raw.get("certificate_id") == certificate_id:
            try:
                result = load_and_verify(candidate, store=STORE)
                return _envelope(status="ok", data=result)
            except Exception as exc:  # noqa: BLE001 - verification failure returns error envelope
                return _envelope(status="error", error=_error("CERTIFICATE_INVALID", str(exc)))
    raise HTTPException(status_code=404, detail=_error("INPUT_MISSING", "certificate is not present"))


@app.get("/certificates/{certificate_id}")
def certificate(certificate_id: str):
    candidates = list(ARTIFACT_ROOT.glob("**/*.json"))
    for candidate in candidates:
        try:
            raw = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if raw.get("certificate_id") == certificate_id:
            return _envelope(status="ok", data=raw)
    raise HTTPException(status_code=404, detail=_error("INPUT_MISSING", "certificate is not present"))


@app.get("/adapter-status")
def adapter_status() -> dict[str, Any]:
    return {**LAST_ADAPTER_STATUS, "scope": "weather-context-only", "no_authoritative_fields": True}


@app.post("/demo")
def demo(request: DemoRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    previous = _idempotency(idempotency_key, request.idempotency_key, request.model_dump(mode="python"))
    if previous:
        return previous[1]
    execution = execute_replay(seed=request.seed, policy_mode=request.policy_mode, adapter_state=request.adapter_state, deadline_seconds=request.deadline_seconds)
    LAST_ADAPTER_STATUS.update({"state": execution.adapter_state, "source": execution.adapter_status.source, "content_sha256": execution.adapter_status.content_sha256, "reason": execution.adapter_status.reason})
    path = write_execution_artifacts(execution)
    response = _envelope(status="ok", data={"replay_id": execution.replay_id, "elapsed_seconds": execution.elapsed_seconds, "summary_path": str(path), "scenario_id": execution.scenarios.realized_path_id})
    _remember(request.idempotency_key, request.model_dump(mode="python"), response)
    return response


@app.get("/")
def root_redirect() -> Any:
    return RedirectResponse(url="/ui/")


@app.get("/api/portfolio")
def get_portfolio() -> dict[str, Any]:
    portfolio = load_demo_portfolio()
    return _envelope(status="ok", data=portfolio.model_dump(mode="json"))


from functools import lru_cache


@lru_cache(maxsize=4)
def _sim_context(seed: int):
    """Portfolio, events, and the realized scenario path for one seed (replay-free)."""
    from .scenarios import generate_scenarios

    portfolio = load_demo_portfolio()
    events = load_demo_events()
    scenario_set = generate_scenarios(portfolio, seed=seed)
    return portfolio, events, scenario_set.paths[1]


@app.get("/api/simulation/state")
def get_simulation_state(slot: int = 0, replay_id: str | None = None) -> dict[str, Any]:
    from .dispatch import available_generation, demand_by_load, effective_tie_capacity

    if slot < 0 or slot > 95:
        raise HTTPException(status_code=422, detail=_error("INPUT_INVALID", "slot must be in [0, 95]"))

    record = None
    state_source = "fixture_initial_conditions"
    if replay_id is not None:
        record = REPLAYS.get(replay_id)
        if record is None:
            raise HTTPException(status_code=404, detail=_error("INPUT_MISSING", "replay is not present", replay_id=replay_id))
        seed = record.execution.scenarios.seed
        state_source = "replay"
    else:
        seed = load_demo_portfolio().seed

    portfolio, events, path = _sim_context(seed)
    grid = next(a for a in portfolio.assets if a.kind == "grid_interface")

    available = available_generation(portfolio, path, events, slot)
    solar_ids = [a.asset_id for a in portfolio.assets_of("solar_farm")]
    wind_ids = [a.asset_id for a in portfolio.assets_of("wind_farm")]
    solar_gen = {aid: available.get(aid, 0.0) for aid in solar_ids}
    wind_gen = {aid: available.get(aid, 0.0) for aid in wind_ids}
    total_solar = sum(solar_gen.values())
    total_wind = sum(wind_gen.values())
    total_re = round(sum(available.values()), 6)

    loads = demand_by_load(portfolio, path, events, slot)
    total_demand = round(sum(loads.values()), 6)
    tie_capacity = effective_tie_capacity(portfolio, events, slot)
    price = path.price_usd_per_mwh[slot]

    if record is not None:
        state = record.execution.twin.slot_states[slot]
        flow = state.flow_mw
        bess_energy = state.bess_energy_mwh
    else:
        flow = portfolio.initial_flow_mw
        bess_energy = {b.asset_id: b.initial_energy_mwh for b in portfolio.assets_of("bess")}

    bess_state = {}
    for b in portfolio.assets_of("bess"):
        energy = bess_energy.get(b.asset_id, b.initial_energy_mwh)
        bess_state[b.asset_id] = {
            "energy_mwh": round(energy, 6),
            "soc_fraction": round(energy / b.capacity_mwh, 6),
            "capacity_mwh": b.capacity_mwh,
            "reserve_mwh": b.reserve_mwh,
        }

    active_events = [e.model_dump(mode="json") for e in events if e.start_slot <= slot <= e.end_slot]

    return _envelope(
        status="ok",
        data={
            "slot_index": slot,
            "state_source": state_source,
            "solar_generation_mw": solar_gen,
            "wind_generation_mw": wind_gen,
            "total_solar_mw": round(total_solar, 6),
            "total_wind_mw": round(total_wind, 6),
            "total_renewable_mw": total_re,
            "industrial_demand_mw": {**loads, "total_mw": total_demand},
            "market_price_usd_per_mwh": round(price, 6),
            "bess_state": bess_state,
            "grid_tie": {
                "capacity_mw": round(tie_capacity, 6),
                "import_limit_mw": grid.import_limit_mw,
                "export_limit_mw": grid.export_limit_mw,
                "flow_mw": round(flow, 6),
                "headroom_mw": round(tie_capacity - abs(flow), 6),
            },
            "active_events": active_events,
        },
    )


@app.post("/api/deliberate")
def deliberate_slot(body: dict[str, Any]) -> dict[str, Any]:
    # Route path retained for the auth policy table (auth.py is protected and
    # keys its request policy on this path). The behaviour is narration-only:
    # the planner and validator are run here, then the DecisionNarrator only
    # DESCRIBES their output. See narration.DISCLAIMER.
    from .narration import DISCLAIMER, DecisionNarrator
    from .scenarios import generate_scenarios

    portfolio = load_demo_portfolio()
    scenarios = generate_scenarios(portfolio)
    slot_idx = min(95, max(0, int(body.get("slot_index", 0))))
    policy = body.get("policy_mode", "balanced")
    demo_events = [e for e in load_demo_events() if e.start_slot <= slot_idx <= e.end_slot]
    path = scenarios.paths[1]

    plan_result = plan_next(
        portfolio,
        path,
        demo_events,
        replay_id=f"narration-{slot_idx}",
        slot_index=slot_idx,
        policy_mode=policy,
        energy_state=body.get("energy_state"),
        scenario_paths=scenarios.paths,
    )
    safety_decision = validate_proposal(
        portfolio,
        path,
        demo_events,
        plan_result.proposal,
        replay_id=f"narration-{slot_idx}",
        slot_index=slot_idx,
        energy_state=body.get("energy_state"),
    )
    narration = DecisionNarrator(portfolio).narrate_decision(
        path,
        demo_events,
        replay_id=f"narration-{slot_idx}",
        slot_index=slot_idx,
        policy_mode=policy,
        plan_result=plan_result,
        safety_decision=safety_decision,
        energy_state=body.get("energy_state"),
    )

    return _envelope(
        status="ok",
        data={
            "slot_index": slot_idx,
            "policy_mode": policy,
            "narrative": narration.consensus_rationale,
            "observations": [t.__dict__ for t in narration.thoughts],
            "disclaimer": DISCLAIMER,
            "proposal": narration.plan_result.proposal.model_dump(mode="json"),
            "safety_decision": narration.safety_decision.model_dump(mode="json"),
        },
    )


@app.post("/api/multimodal/document")
def parse_multimodal_document(body: dict[str, Any]) -> dict[str, Any]:
    from .multimodal import DocumentExtractorEngine

    extractor = DocumentExtractorEngine()
    text = body.get("text_content", "")
    source = body.get("source_label", "document_upload")
    directives = extractor.parse_document(text, source_label=source)
    return _envelope(status="ok", data={"parsed_directives": [d.__dict__ for d in directives]})


@app.post("/api/multimodal/nowcast")
def parse_multimodal_nowcast(body: dict[str, Any]) -> dict[str, Any]:
    from .multimodal import SkyCameraFrame, VisionNowcastEngine

    # B3: velocity is computed from two supplied frames; no hardcoded vector.
    if "frame_pixels_previous" not in body or "previous_timestamp_utc" not in body:
        raise HTTPException(status_code=422, detail=_error("INPUT_MISSING", "frame_pixels_previous and previous_timestamp_utc are required for a two-frame nowcast"))
    if body.get("timestamp_utc") == body.get("previous_timestamp_utc"):
        raise HTTPException(status_code=422, detail=_error("INPUT_INVALID", "the two frame timestamps must differ"))

    engine = VisionNowcastEngine()
    res = tuple(body.get("resolution", [64, 64]))
    frame_prev = SkyCameraFrame(
        timestamp_utc=body["previous_timestamp_utc"],
        resolution=res,
        raw_pixels=body["frame_pixels_previous"],
        camera_id=body.get("camera_id", "sky-cam-01"),
    )
    frame = SkyCameraFrame(
        timestamp_utc=body.get("timestamp_utc", "2026-07-01T12:00:00Z"),
        resolution=res,
        raw_pixels=body.get("frame_pixels", [0.5] * (res[0] * res[1])),
        camera_id=body.get("camera_id", "sky-cam-01"),
    )
    velocity = engine.compute_optical_flow_velocity(frame_prev, frame, time_delta_seconds=float(body.get("time_delta_seconds", 60.0)))
    portfolio = load_demo_portfolio()
    fleet_mw = sum(a.capacity_mw for a in portfolio.assets_of("solar_farm"))
    res_nowcast = engine.project_solar_attenuation(
        frame, velocity, fleet_mw, lead_time_minutes=float(body.get("lead_time_minutes", 15.0))
    )
    return _envelope(
        status="ok",
        data={
            "velocity_vector_ms": res_nowcast.velocity_vector_ms,
            "mean_cloud_cover_fraction": res_nowcast.mean_cloud_cover_fraction,
            "affected_solar_assets": res_nowcast.affected_solar_assets,
            "predicted_ramp_rate_mw_min": res_nowcast.predicted_ramp_rate_mw_min,
            "match_strength": res_nowcast.match_strength,
        },
    )


UI_ROOT = Path(__file__).resolve().parent / "ui"
if UI_ROOT.exists():
    app.mount("/ui", StaticFiles(directory=UI_ROOT, html=True), name="ui")
