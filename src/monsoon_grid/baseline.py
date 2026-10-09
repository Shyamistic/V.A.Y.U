"""Greedy/P50 baseline on the same realized scenario path."""

from __future__ import annotations

from .assumptions import CarbonFactorAssumption, load_carbon_factor
from .canonical import sha256_hex
from .contracts import ActionProposal, ApprovalEvent, EventRecord, PortfolioFixture, Provenance, SafetyDecision, ScenarioPath
from .equations import bess_energy_next
from .fallback import fallback_plan
from .twin import TwinResult, run_twin
from .validator import validate_proposal


def _approval(replay_id: str, slot: int, proposal: ActionProposal, energy: dict[str, float], flow: float) -> ApprovalEvent:
    return ApprovalEvent(
        replay_id=replay_id,
        decision_id=proposal.decision_id,
        slot_index=slot,
        actor_id="batch-replay-synthetic",
        reason="deterministic baseline approval for comparison only",
        synthetic_approval=True,
        proposal_digest=sha256_hex(proposal),
        current_state_digest=sha256_hex({"replay_id": replay_id, "slot_index": slot, "bess_energy_mwh": dict(sorted(energy.items())), "previous_flow_mw": round(flow, 6)}),
        provenance=Provenance(label="scenario_assumption", source_id="batch-replay-synthetic-approval", source_version="greedy-p50-baseline-v2", input_refs=[]),
    )


def run_greedy_p50_baseline(portfolio: PortfolioFixture, path: ScenarioPath, events: list[EventRecord], *, replay_id: str, carbon_factor: CarbonFactorAssumption | None = None) -> TwinResult:
    proposals: dict[int, ActionProposal] = {}
    decisions: dict[int, SafetyDecision] = {}
    approvals: dict[int, ApprovalEvent] = {}
    energy = {asset.asset_id: asset.initial_energy_mwh for asset in portfolio.assets if asset.kind == "bess"}
    flow = portfolio.initial_flow_mw
    for slot in range(96):
        proposal, _, _ = fallback_plan(portfolio, path, events, replay_id=f"{replay_id}:baseline", slot_index=slot, policy_mode="balanced", energy_state=energy, previous_flow_mw=flow, reason="greedy-p50-baseline")
        decision = validate_proposal(portfolio, path, events, proposal, replay_id=f"{replay_id}:baseline", slot_index=slot, energy_state=energy, previous_flow_mw=flow)
        proposals[slot] = proposal
        decisions[slot] = decision
        if decision.status == "ACCEPTED":
            approvals[slot] = _approval(f"{replay_id}:baseline", slot, proposal, energy, flow)
            for asset in portfolio.assets:
                if asset.kind == "bess":
                    energy[asset.asset_id] = bess_energy_next(energy[asset.asset_id], proposal.bess_charge_mw[asset.asset_id][0], proposal.bess_discharge_mw[asset.asset_id][0], asset)
            flow = proposal.grid_import_mw[0] - proposal.grid_export_mw[0]
    return run_twin(
        portfolio,
        path,
        events,
        proposals,
        decisions,
        replay_id=f"{replay_id}:baseline",
        applied_slots={slot for slot, decision in decisions.items() if decision.status == "ACCEPTED"},
        approvals=approvals,
        carbon_factor=carbon_factor or load_carbon_factor(),
    )
