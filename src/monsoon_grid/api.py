"""Local FastAPI service with typed replay-only semantics."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import auth, observability, tenancy
from .canonical import canonical_text, sha256_hex, state_digest
from .certificates import ensure_demo_trust
from .cli import execute_replay, write_execution_artifacts
from .config import ARTIFACT_ROOT, PRODUCT_ROOT, REPLAY_VERSION
from .contracts import RESERVED_ACTOR_IDS, ApprovalEvent, ApprovalRecord, ApprovalRequest, DemoRequest, PlanningRequest, Provenance, ReplayAdvanceRequest, ReplayCreateRequest, SafetyDecision, ValidationRequest
from .forecasts import build_forecasts
from .fixtures import fixture_set_hash, load_demo_events, load_demo_portfolio
from .cli import _approval_audit_event
from .persistence import PersistenceStore
from .planner import plan_next
from .validator import validate_proposal
from .workspace_evidence import assemble_workspace_evidence

app = FastAPI(title="MonsoonGrid P4", version=REPLAY_VERSION, description="Local SIMULATION/REPLAY service; no live control or market submission.")


@dataclass
class ReplayRecord:
    execution: Any
    slot_index: int = 0
    approval_state: str = "PENDING"
    state_digest: str = ""
    approval_events: dict[int, ApprovalEvent] | None = None
    tenant_id: str = tenancy.DEFAULT_TENANT_ID


def _caller_tenant() -> str:
    context = observability.current_context()
    return context.tenant if context is not None and context.tenant else tenancy.DEFAULT_TENANT_ID


class ReplayRegistry(dict[str, ReplayRecord]):
    def get(self, key: str, default=None):
        record = super().get(key, default)
        if record is not None and record.tenant_id != _caller_tenant():
            return default
        return record

    def __setitem__(self, key: str, record: ReplayRecord) -> None:
        existing = super().get(key)
        if existing is not None and existing.tenant_id != _caller_tenant():
            raise HTTPException(status_code=409, detail=_error("STATE_CONFLICT", "replay identity is unavailable"))
        record.tenant_id = _caller_tenant()
        super().__setitem__(key, record)


REPLAYS: dict[str, ReplayRecord] = ReplayRegistry()
IDEMPOTENCY: dict[str, tuple[str, Any]] = {}
LAST_ADAPTER_STATUS: dict[str, Any] = {"state": "DISABLED", "source": "none", "content_sha256": None, "reason": "VAYU context adapter disabled by default", "scope": "weather-context-only", "no_authoritative_fields": True}
STORE = PersistenceStore()
DECISION_AUTHORS = auth.DecisionAuthorIndex()


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
    header = _scoped_key(header)
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


def _scoped_key(key: str) -> str:
    context = observability.current_context()
    if context is None or not context.principal:
        return key
    return sha256_hex({"tenant": _caller_tenant(), "principal": context.principal, "key": key})


def _require_pilot_artifacts() -> None:
    if _caller_tenant() != tenancy.DEFAULT_TENANT_ID:
        raise HTTPException(status_code=404, detail=_error("INPUT_MISSING", "evidence is not present"))


def _remember(key: str, payload: Any, response: Any) -> None:
    key = _scoped_key(key)
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
def advance_replay(replay_id: str, request: ReplayAdvanceRequest, http_request: Request, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
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
    principal = getattr(http_request.state, "principal", None)
    if principal is not None and DECISION_AUTHORS.author_of(replay_id=replay_id, decision_id=decision.decision_id) is None:
        DECISION_AUTHORS.record_author(
            replay_id=replay_id, decision_id=decision.decision_id,
            subject=principal.subject, author_class="human",
        )
    approval = (record.approval_events or {}).get(record.slot_index)
    # A5: a mutation requires a PERSISTED approval, not just an in-memory one.
    persisted = STORE.load_approval(replay_id, decision.decision_id) is not None
    token_matches = (
        approval is not None and persisted
        and approval.slot_index == record.slot_index
        and approval.proposal_digest == sha256_hex(proposal)
        and request.approval_token in {approval.record_id, approval.proposal_digest}
    )
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
def approval(request: ApprovalRequest, http_request: Request, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    principal = getattr(http_request.state, "principal", None)
    if principal is not None:
        request = request.model_copy(update={"actor_id": principal.actor_id})
    previous = _idempotency(idempotency_key, request.idempotency_key, request.model_dump(mode="python"))
    if previous:
        return previous[1]
    record = REPLAYS.get(request.replay_id)
    if record is None or request.expected_state_digest != _state_digest(record):
        raise HTTPException(status_code=409, detail=_error("STATE_CONFLICT", "replay or expected digest is stale", replay_id=request.replay_id, retryable=True))
    if principal is not None:
        authorization = auth.authorize_approval(
            principal=principal, replay_id=request.replay_id, decision_id=request.decision_id,
            expected_state_digest=request.expected_state_digest, observed_state_digest=_state_digest(record),
            idempotency_key=request.idempotency_key, action=request.action, reason=request.reason or "",
            author=DECISION_AUTHORS.author_of(replay_id=request.replay_id, decision_id=request.decision_id),
            now=auth._utcnow(), production=getattr(http_request.state, "auth_environment", "production") == "production",
        )
        if isinstance(authorization, auth.ApprovalRejected):
            status_code = 403 if authorization.code in {"APPROVAL_ROLE_DENIED", "APPROVAL_SELF_APPROVAL", "APPROVAL_AUTHOR_UNKNOWN"} else 422
            raise HTTPException(status_code=status_code, detail=_error(authorization.code, authorization.message, retryable=authorization.retryable))
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
def planning(request: PlanningRequest, http_request: Request, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    previous = _idempotency(idempotency_key, request.idempotency_key, request.model_dump(mode="python"))
    if previous:
        return previous[1]
    record = REPLAYS.get(request.replay_id)
    if record is None or request.expected_state_digest != _state_digest(record) or request.slot_index != record.slot_index:
        raise HTTPException(status_code=409, detail=_error("STATE_CONFLICT", "planning state is stale", replay_id=request.replay_id, retryable=True))
    path = next(item for item in record.execution.scenarios.paths if item.scenario_id == record.execution.scenarios.realized_path_id)
    result = plan_next(record.execution.portfolio, path, record.execution.events, replay_id=request.replay_id, slot_index=request.slot_index, policy_mode=request.policy_mode)
    if result.proposal is None:
        raise HTTPException(status_code=503, detail=_error("DEPENDENCY_UNAVAILABLE", "planner returned no proposal", retryable=True))
    decision = validate_proposal(record.execution.portfolio, path, record.execution.events, result.proposal, replay_id=request.replay_id, slot_index=request.slot_index)
    record.execution.decisions[request.slot_index] = decision
    record.execution.proposals[request.slot_index] = result.proposal
    principal = getattr(http_request.state, "principal", None)
    if principal is not None and DECISION_AUTHORS.author_of(replay_id=request.replay_id, decision_id=result.proposal.decision_id) is None:
        DECISION_AUTHORS.record_author(replay_id=request.replay_id, decision_id=result.proposal.decision_id, subject=principal.subject, author_class="human")
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
    _require_pilot_artifacts()
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
    _require_pilot_artifacts()
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
    _require_pilot_artifacts()
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
    _require_pilot_artifacts()
    previous = _idempotency(idempotency_key, request.idempotency_key, request.model_dump(mode="python"))
    if previous:
        return previous[1]
    execution = execute_replay(seed=request.seed, policy_mode=request.policy_mode, adapter_state=request.adapter_state, deadline_seconds=request.deadline_seconds)
    LAST_ADAPTER_STATUS.update({"state": execution.adapter_state, "source": execution.adapter_status.source, "content_sha256": execution.adapter_status.content_sha256, "reason": execution.adapter_status.reason})
    path = write_execution_artifacts(execution)
    register_execution(execution)
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


@app.get("/api/session")
def session(http_request: Request) -> dict[str, Any]:
    principal = auth.current_principal(http_request)
    return _envelope(status="ok", data={
        "actor_id": principal.actor_id, "tenant_id": principal.tenant_id,
        "roles": sorted(principal.roles), "auth_method": principal.auth_method,
        "environment": getattr(http_request.state, "auth_environment", "development"),
    })


@app.get("/api/workspace")
def workspace() -> dict[str, Any]:
    _require_pilot_artifacts()
    try:
        replay = json.loads((ARTIFACT_ROOT / "replay-results.json").read_text(encoding="utf-8"))
        summary = json.loads((ARTIFACT_ROOT / "demo-summary.json").read_text(encoding="utf-8"))
        metric_data = json.loads((ARTIFACT_ROOT / "metrics.json").read_text(encoding="utf-8"))
        certificate_data = json.loads((ARTIFACT_ROOT / "certificate.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail=_error("EVIDENCE_UNAVAILABLE", "generate the local replay evidence before opening the workspace", retryable=True)) from exc
    if len(replay.get("slot_states", [])) != 96 or replay.get("replay_id") != summary.get("replay_id") or metric_data != summary.get("metrics"):
        raise HTTPException(status_code=503, detail=_error("EVIDENCE_INCONSISTENT", "replay evidence must be regenerated together", retryable=True))
    try:
        decisions = replay["decisions"]
        if isinstance(decisions, dict):
            decisions = list(decisions.values())
        replay["decisions"] = [item.model_dump(mode="json") for item in sorted(
            (SafetyDecision.model_validate(item) for item in decisions), key=lambda item: item.slot_index,
        )]
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=_error("EVIDENCE_INCONSISTENT", "replay decisions do not satisfy the workspace contract", retryable=True)) from exc
    portfolio_data = load_demo_portfolio()
    try:
        evidence = assemble_workspace_evidence(portfolio_data, replay, PRODUCT_ROOT / "experiments" / "results", metric_data)
    except (KeyError, TypeError, ValueError, StopIteration) as exc:
        raise HTTPException(status_code=503, detail=_error("EVIDENCE_INCONSISTENT", "scenario evidence does not satisfy the workspace contract", retryable=True)) from exc
    return _envelope(status="ok", data={
        "portfolio": portfolio_data.model_dump(mode="json"),
        "replay": replay, "summary": summary, "metrics": metric_data,
        "certificate": certificate_data,
        **evidence,
        "scope": "SYNTHETIC_REPLAY", "layout": "SCHEMATIC_NOT_GEOGRAPHIC",
        "limitations": ["G3_NON_ANTICIPATIVITY", "DSM_RATES_ASSUMED", "NO_LIVE_CONTROL", "NO_MARKET_SUBMISSION"],
    })


@lru_cache(maxsize=4)
def _sim_context(seed: int):
    """Portfolio, events, and the realized scenario path for one seed (replay-free)."""
    from .scenarios import generate_scenarios

    portfolio = load_demo_portfolio()
    events = load_demo_events()
    scenario_set = generate_scenarios(portfolio, seed=seed)
    return portfolio, events, scenario_set.paths[1]


@lru_cache(maxsize=8)
def _agent_decision_cached(seed: int, slot: int, preset: str, brain_name: str) -> str:
    """Run the objective-selection agent for a slot and cache the JSON result.

    Cached because the deterministic brain + solver are pure for a (seed, slot,
    preset); a Gemini brain is only used when explicitly enabled via env.
    """
    import json as _json

    from .agent import ObjectiveSelectionAgent, build_brain
    from .dispatch import available_generation, demand_by_load
    from .forecasts import build_forecasts

    portfolio, events, path = _sim_context(seed)
    generation: dict[str, list[float]] = {}
    demand: dict[str, list[float]] = {}
    for s in range(96):
        for key, value in available_generation(portfolio, path, events, s).items():
            generation.setdefault(key, [0.0] * 96)[s] = value
        for key, value in demand_by_load(portfolio, path, events, s).items():
            demand.setdefault(key, [0.0] * 96)[s] = value
    forecasts = build_forecasts(portfolio)
    timestamp = replay_time_iso(portfolio.fixture_start_utc, slot)
    agent = ObjectiveSelectionAgent(portfolio=portfolio, brain=build_brain(brain_name))
    decision = agent.deliberate(
        path, events, replay_id=f"agent-{seed}", slot_index=slot,
        generation_mw=generation, demand_mw=demand, forecasts=forecasts,
        energy_state=None, preset_policy=preset, timestamp_utc=timestamp,
    )
    payload = {
        "replay_id": decision.replay_id, "slot_index": decision.slot_index, "brain": decision.brain,
        "features": decision.features.as_dict(),
        "chosen_objective_order": decision.chosen_objective_order,
        "rationale": decision.rationale, "confidence": decision.confidence,
        "steps": [
            {"iteration": s.iteration, "objective_order": s.objective_order, "rationale": s.rationale,
             "confidence": s.confidence, "validator_status": s.validator_status,
             "critical_unserved_mwh": s.critical_unserved_mwh, "net_cost_usd": s.net_cost_usd,
             "accepted": s.accepted, "revised_reason": s.revised_reason}
            for s in decision.steps
        ],
        "actions": [
            {"category": a.category, "action": a.action, "target": a.target,
             "magnitude_mw": a.magnitude_mw, "unit": a.unit, "driver_objective": a.driver_objective, "note": a.note}
            for a in decision.actions
        ],
        "conflict_note": decision.conflict_note,
        "preset_policy": decision.preset_policy,
        "preset_net_cost_usd": decision.preset_net_cost_usd,
        "preset_critical_unserved_mwh": decision.preset_critical_unserved_mwh,
        "agent_net_cost_usd": decision.agent_net_cost_usd,
        "agent_critical_unserved_mwh": decision.agent_critical_unserved_mwh,
        "cost_regret_usd": decision.cost_regret_usd,
        "reliability_regret_mwh": decision.reliability_regret_mwh,
        "forbidden_tools_blocked": decision.forbidden_tools_blocked,
        "digest": decision.digest,
        "scope": "SIMULATION / REPLAY / NO LIVE CONTROL / NO MARKET SUBMISSION",
    }
    return _json.dumps(payload)


def replay_time_iso(start, slot_index: int) -> str:
    from .quality import replay_time
    return replay_time(start, slot_index).isoformat()


@app.get("/api/agent/deliberate")
def agent_deliberate(slot: int = 0, policy: str = "balanced") -> dict[str, Any]:
    """Autonomous objective-selection (P4 F2): the agent chooses the optimizer's
    criteria for this slot, reflects on the validator outcome, and reports regret
    vs the fixed preset. Read/simulation only; no command path."""
    import json as _json

    _require_pilot_artifacts()
    if slot < 0 or slot > 95:
        raise HTTPException(status_code=422, detail=_error("INPUT_INVALID", "slot must be in [0, 95]"))
    if policy not in {"reliability_first", "balanced", "cost_first"}:
        raise HTTPException(status_code=422, detail=_error("INPUT_INVALID", "policy must be a known preset"))
    import os as _os

    brain = _os.environ.get("VAYU_AGENT_BRAIN", "deterministic")
    seed = load_demo_portfolio().seed
    try:
        payload = _json.loads(_agent_decision_cached(seed, slot, policy, brain))
    except Exception as exc:  # noqa: BLE001 - surface a typed error, never a 500 stack
        raise HTTPException(status_code=503, detail=_error("AGENT_UNAVAILABLE", f"agent deliberation failed: {type(exc).__name__}", retryable=True)) from exc
    return _envelope(status="ok", data=payload)


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


UI_ROOT = Path(__file__).resolve().parent / "ui" / "dist"
if UI_ROOT.exists():
    app.mount("/ui", StaticFiles(directory=UI_ROOT, html=True), name="ui")
