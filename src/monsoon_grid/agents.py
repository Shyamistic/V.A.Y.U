"""Typed, allowlisted orchestration services; no free text becomes authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .contracts import ActionProposal, EventRecord, PlanResult, PolicyMode, PortfolioFixture, ScenarioPath, SafetyDecision
from .planner import plan_next
from .validator import validate_proposal

ALLOWLISTED_TOOLS = frozenset({"get_quality", "record_event", "request_forecast", "request_replan", "validate_proposal", "write_certificate"})
FORBIDDEN_TOOLS = frozenset({"write_setpoint", "submit_market_order", "approve_self", "scada_command", "relay_command"})


@dataclass(frozen=True)
class PlanningOrchestrator:
    portfolio: PortfolioFixture

    def request_replan(self, path: ScenarioPath, events: Iterable[EventRecord], *, replay_id: str, slot_index: int, policy_mode: PolicyMode, energy_state: dict[str, float] | None = None, previous_flow_mw: float = 0.0) -> PlanResult:
        return plan_next(self.portfolio, path, events, replay_id=replay_id, slot_index=slot_index, policy_mode=policy_mode, energy_state=energy_state, previous_flow_mw=previous_flow_mw)

    def validate(self, path: ScenarioPath, events: Iterable[EventRecord], proposal: ActionProposal, *, replay_id: str, slot_index: int, energy_state: dict[str, float] | None = None, previous_flow_mw: float = 0.0) -> SafetyDecision:
        return validate_proposal(self.portfolio, path, events, proposal, replay_id=replay_id, slot_index=slot_index, energy_state=energy_state, previous_flow_mw=previous_flow_mw)


@dataclass(frozen=True)
class ExplanationService:
    """Renders only facts already present in typed solver/validator records."""

    def render(self, decision: SafetyDecision) -> dict[str, object]:
        return {"decision_id": decision.decision_id, "status": decision.status, "violations": [violation.code for violation in decision.violations], "critical_unserved_mwh": decision.critical_unserved_mwh, "reserve_shortfall_mwh": decision.reserve_shortfall_mwh}


@dataclass(frozen=True)
class ApprovalService:
    def can_approve(self, decision: SafetyDecision) -> bool:
        return decision.status == "ACCEPTED" and decision.approved and decision.critical_unserved_mwh <= 0.000001 and decision.reserve_shortfall_mwh <= 0.000001
