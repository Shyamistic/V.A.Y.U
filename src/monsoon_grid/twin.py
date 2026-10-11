"""Deterministic 96-slot software energy twin with an independent apply gate."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Iterable, Mapping

from .assumptions import (
    CarbonFactorAssumption,
    FlexibilityCostAssumption,
    MarketSettlementAssumption,
    load_carbon_factor,
    load_flexibility_costs,
    load_market_settlement,
)
from .canonical import sha256_hex
from .config import DT_HOURS, SLOT_COUNT
from .contracts import ActionProposal, ApprovalEvent, BessSpec, EventRecord, GridInterfaceSpec, LoadClassSpec, Metrics, Provenance, QualityDecision, SafetyDecision, ScenarioPath, PortfolioFixture, TwinSlotState
from .dispatch import available_generation, demand_by_load, next_energy, partition_unserved, slot_supply_mw
from .equations import slot_cost_rates
from .errors import TwinInvariantError
from . import market
from .metrics import build_metric
from .validator import validate_proposal

TWIN_VERSION = "software-energy-twin-v2"


def _prov(*refs: str) -> Provenance:
    return Provenance(label="derived", source_id="software-twin", source_version=TWIN_VERSION, input_refs=list(refs) or ["software-twin"])


@dataclass(frozen=True)
class TwinResult:
    slot_states: list[TwinSlotState]
    metrics: Metrics
    final_energy_mwh: dict[str, float]
    final_flow_mw: float


def _approval_state_digest(replay_id: str, slot: int, energies: Mapping[str, float], flow: float) -> str:
    return sha256_hex({"replay_id": replay_id, "slot_index": slot, "bess_energy_mwh": dict(sorted(energies.items())), "previous_flow_mw": round(flow, 6)})


def run_twin(
    portfolio: PortfolioFixture,
    path: ScenarioPath,
    events: Iterable[EventRecord],
    proposals: Mapping[int, ActionProposal],
    decisions: Mapping[int, SafetyDecision],
    *,
    replay_id: str,
    applied_slots: set[int] | None = None,
    approvals: Mapping[int, ApprovalEvent] | None = None,
    quality_by_slot: Mapping[int, Iterable[QualityDecision]] | None = None,
    carbon_factor: CarbonFactorAssumption | None = None,
    flexibility: FlexibilityCostAssumption | None = None,
    settlement: MarketSettlementAssumption | None = None,
) -> TwinResult:
    """Apply only validator-accepted proposals with matching human evidence.

    ``applied_slots`` is an optional replay selection, not an approval bypass.
    Every mutation requires a matching ApprovalEvent and a second validator
    evaluation against the state immediately before the mutation.
    """
    events = list(events)
    applied_slots = set() if applied_slots is None else set(applied_slots)
    approvals = approvals or {}
    quality_by_slot = quality_by_slot or {}
    carbon_factor = carbon_factor or load_carbon_factor()
    flexibility = flexibility or load_flexibility_costs()
    settlement = settlement or load_market_settlement()
    commitments = list(portfolio.market_commitments)
    grid: GridInterfaceSpec = next(asset for asset in portfolio.assets if isinstance(asset, GridInterfaceSpec))
    load_specs: list[LoadClassSpec] = [asset for asset in portfolio.assets if isinstance(asset, LoadClassSpec)]
    flex_shed = {entry.load_id: entry.shed_cost_usd_per_mwh for entry in flexibility.entries}
    flex_shift = {entry.load_id: entry.shift_cost_usd_per_mwh for entry in flexibility.entries}

    energies = {asset.asset_id: asset.initial_energy_mwh for asset in portfolio.assets if isinstance(asset, BessSpec)}
    previous_flow = portfolio.initial_flow_mw
    slot_states: list[TwinSlotState] = []
    total_cost = 0.0
    total_carbon = 0.0
    total_import = 0.0
    total_export = 0.0
    total_available = 0.0
    total_dispatched = 0.0
    total_curtailment = 0.0
    total_unserved_critical = 0.0
    total_unserved_noncritical = 0.0
    total_reserve_shortfall = 0.0
    total_dr_shed = 0.0
    total_dr_shift = 0.0
    total_dr_rebound = 0.0
    total_dr_cost = 0.0
    total_noncritical_reduction = 0.0
    total_market_revenue = 0.0
    total_nonmarket_valuation = 0.0
    total_market_shortfall_mwh = 0.0
    total_market_shortfall_penalty = 0.0
    total_committed_export = 0.0
    total_uncommitted_export = 0.0
    total_validator_predicted_critical = 0.0
    contracted_ids = {contract.load_id for contract in portfolio.dr_contracts}
    contracted_numer = 0.0
    contracted_denom = 0.0
    throughput = 0.0
    safety_interventions = 0
    planned_fallback_count = 0
    applied_fallback_count = 0
    unapplied_with_unserved = 0

    for slot in range(SLOT_COUNT):
        proposal = proposals.get(slot)
        decision = decisions.get(slot)
        active_events = [event for event in events if event.start_slot <= slot <= event.end_slot]
        active_ids = [event.event_id for event in active_events]
        available = available_generation(portfolio, path, events, slot)
        loads = demand_by_load(portfolio, path, events, slot)
        demand_total_mw = sum(loads.values())
        demand_mwh = round(demand_total_mw, 6) * DT_HOURS

        if proposal is not None and proposal.solver_result.status != "OPTIMAL":
            planned_fallback_count += 1

        action_applied = False
        renewable_dispatch = 0.0
        import_mw = 0.0
        export_mw = 0.0
        shed_by: dict[str, float] = {}
        shift_down_by: dict[str, float] = {}
        shift_up_total = 0.0
        rebound_total = 0.0
        reduce_by: dict[str, float] = {}
        charge_by: dict[str, float] = {}
        discharge_by: dict[str, float] = {}
        # No-action defaults (A1): zero supply, whole demand unserved.
        supply_mw = 0.0
        unserved_mwh = demand_mwh
        proposal_id = proposal.proposal_id if proposal else None
        decision_id = decision.decision_id if decision else None

        if decision is not None:
            total_validator_predicted_critical += getattr(decision, "critical_unserved_mwh", 0.0) or 0.0

        approval = approvals.get(slot)
        if proposal is not None and decision is not None and slot in applied_slots and decision.status == "ACCEPTED" and approval is not None:
            expected_state = _approval_state_digest(replay_id, slot, energies, previous_flow)
            current_proposal_digest = sha256_hex(proposal)
            quality_decisions = list(quality_by_slot.get(slot, ()))
            independent = validate_proposal(
                portfolio,
                path,
                events,
                proposal,
                replay_id=replay_id,
                slot_index=slot,
                energy_state=dict(energies),
                previous_flow_mw=previous_flow,
                quality_decisions=quality_decisions,
            )
            if (
                approval.replay_id == replay_id
                and approval.decision_id == decision.decision_id
                and approval.proposal_digest == current_proposal_digest
                and approval.current_state_digest == expected_state
                and independent.status == "ACCEPTED"
            ):
                renewable_dispatch = sum(value[0] for value in proposal.renewable_dispatch_mw.values())
                import_mw = proposal.grid_import_mw[0]
                export_mw = proposal.grid_export_mw[0]
                charge_by = {asset_id: values[0] for asset_id, values in proposal.bess_charge_mw.items()}
                discharge_by = {asset_id: values[0] for asset_id, values in proposal.bess_discharge_mw.items()}
                shed_by = {load_id: values[0] for load_id, values in proposal.shed_mw.items()}
                shift_down_by = {load_id: values[0] for load_id, values in proposal.shift_down_mw.items()}
                shift_up_total = sum(values[0] for values in proposal.shift_up_mw.values())
                rebound_total = sum(values[0] for values in proposal.rebound_mw.values())
                reduce_by = {load_id: values[0] for load_id, values in (proposal.reduce_mw or {}).items()}
                for bess in portfolio.assets_of("bess"):
                    before = energies[bess.asset_id]
                    after = next_energy(before, charge_by.get(bess.asset_id, 0.0), discharge_by.get(bess.asset_id, 0.0), bess)
                    energies[bess.asset_id] = max(0.0, min(bess.capacity_mwh, after))
                    throughput += (charge_by.get(bess.asset_id, 0.0) + discharge_by.get(bess.asset_id, 0.0)) * DT_HOURS
                shed_total = sum(shed_by.values())
                shift_down_total = sum(shift_down_by.values())
                reduce_total = sum(reduce_by.values())
                # Two separately written expressions (A1): deficit vs. supply side.
                balance = (
                    renewable_dispatch
                    + sum(discharge_by.values())
                    + import_mw
                    + shed_total
                    + shift_down_total
                    + reduce_total
                    - demand_total_mw
                    - sum(charge_by.values())
                    - export_mw
                    - shift_up_total
                    - rebound_total
                )
                unserved_mwh = max(0.0, -balance) * DT_HOURS
                supply_mw = slot_supply_mw(
                    renewable_dispatch_mw=renewable_dispatch,
                    discharge_mw=sum(discharge_by.values()),
                    import_mw=import_mw,
                    shed_mw=shed_total,
                    shift_down_mw=shift_down_total,
                    charge_mw=sum(charge_by.values()),
                    export_mw=export_mw,
                    shift_up_mw=shift_up_total,
                    rebound_mw=rebound_total,
                    reduce_mw=reduce_total,
                )
                action_applied = True
                if proposal.solver_result.status != "OPTIMAL":
                    applied_fallback_count += 1
            else:
                safety_interventions += 1
        elif proposal is not None or decision is not None:
            safety_interventions += 1

        served_supply_mwh = min(demand_mwh, max(0.0, supply_mw) * DT_HOURS)
        critical_unserved_mwh, noncritical_unserved_mwh = partition_unserved(unserved_mwh, loads, load_specs)

        # Contracted service fraction accumulation (A2): per contracted load class,
        # served = demand - shed - max(0, shift_down - shift_up_share) - unserved_share.
        crit_cap = sum(loads.get(spec.asset_id, 0.0) for spec in load_specs if spec.critical) * DT_HOURS
        noncrit_cap = sum(loads.get(spec.asset_id, 0.0) for spec in load_specs if not spec.critical) * DT_HOURS
        for spec in load_specs:
            if spec.asset_id not in contracted_ids:
                continue
            demand_l_mwh = loads.get(spec.asset_id, 0.0) * DT_HOURS
            if demand_l_mwh <= 0:
                continue
            if spec.critical:
                share = (demand_l_mwh / crit_cap) * critical_unserved_mwh if crit_cap > 0 else 0.0
            else:
                share = (demand_l_mwh / noncrit_cap) * noncritical_unserved_mwh if noncrit_cap > 0 else 0.0
            shed_l_mwh = shed_by.get(spec.asset_id, 0.0) * DT_HOURS
            defer_l_mwh = max(0.0, shift_down_by.get(spec.asset_id, 0.0)) * DT_HOURS
            served_l = demand_l_mwh - shed_l_mwh - defer_l_mwh - share
            contracted_numer += served_l
            contracted_denom += demand_l_mwh

        # A1 invariant on the UNROUNDED supply-side value.
        residual = served_supply_mwh + critical_unserved_mwh + noncritical_unserved_mwh - demand_mwh
        if abs(residual) > 1e-6:
            raise TwinInvariantError(
                f"slot {slot}: served_supply={served_supply_mwh} critical_unserved={critical_unserved_mwh} "
                f"noncritical_unserved={noncritical_unserved_mwh} demand={demand_mwh} residual={residual}",
                code="TWIN_INVARIANT_VIOLATION",
            )
        if not action_applied and demand_mwh > 0 and (critical_unserved_mwh + noncritical_unserved_mwh) > 0:
            unapplied_with_unserved += 1

        # A1 rounding contract.
        critical_r = round(critical_unserved_mwh, 6)
        noncritical_r = round(noncritical_unserved_mwh, 6)
        served_r = round(max(0.0, demand_mwh - critical_r - noncritical_r), 6)

        # A2 cost decomposition.
        rates = slot_cost_rates(portfolio, path, commitments, settlement, flexibility, slot_index=slot)
        settle = market.settle_slot(
            commitments,
            slot_index=slot,
            export_mw=export_mw,
            nonmarket_export_rate_usd_per_mwh=settlement.nonmarket_export_rate_usd_per_mwh,
            grid_loss_fraction=grid.loss_fraction,
        )
        grid_import_cost = import_mw * DT_HOURS * rates.import_usd_per_mwh
        dr_shed_mwh = sum(shed_by.get(spec.asset_id, 0.0) for spec in load_specs) * DT_HOURS
        dr_shift_mwh = sum(shift_down_by.get(spec.asset_id, 0.0) for spec in load_specs) * DT_HOURS
        dr_rebound_mwh = rebound_total * DT_HOURS
        # F3: unpriced voluntary non-critical reduction, kept OUT of dr_shed_mwh /
        # dr_cost_usd so gate G2 (dr_shed_mwh > 0 => dr_cost_usd > 0) stays valid.
        noncritical_reduction_mwh = sum(reduce_by.get(spec.asset_id, 0.0) for spec in load_specs) * DT_HOURS
        total_noncritical_reduction += noncritical_reduction_mwh
        dr_cost = sum(
            shed_by.get(spec.asset_id, 0.0) * DT_HOURS * flex_shed.get(spec.asset_id, 0.0)
            + shift_down_by.get(spec.asset_id, 0.0) * DT_HOURS * flex_shift.get(spec.asset_id, 0.0)
            for spec in load_specs
        )

        # Six-term cost identity (A2), rounded components for the artifact value.
        grid_import_cost_r = round(grid_import_cost, 6)
        dr_cost_r = round(dr_cost, 6)
        market_shortfall_penalty_r = round(settle.market_shortfall_penalty_usd, 6)
        market_revenue_r = round(settle.market_revenue_usd, 6)
        nonmarket_export_valuation_r = round(settle.nonmarket_export_valuation_usd, 6)
        cost_usd = round(grid_import_cost_r + dr_cost_r + market_shortfall_penalty_r - market_revenue_r - nonmarket_export_valuation_r, 6)

        identity_residual = (
            cost_usd
            - grid_import_cost
            - dr_cost
            - settle.market_shortfall_penalty_usd
            + settle.market_revenue_usd
            + settle.nonmarket_export_valuation_usd
        )
        if abs(identity_residual) > 1e-6:
            raise TwinInvariantError(
                f"slot {slot}: cost_usd={cost_usd} grid_import_cost={grid_import_cost} dr_cost={dr_cost} "
                f"shortfall_penalty={settle.market_shortfall_penalty_usd} market_revenue={settle.market_revenue_usd} "
                f"nonmarket={settle.nonmarket_export_valuation_usd} residual={identity_residual}",
                code="TWIN_COST_IDENTITY_VIOLATION",
            )

        flow = import_mw - export_mw
        available_total = sum(available.values())
        curtailment_mwh = max(0.0, available_total - renewable_dispatch) * DT_HOURS
        carbon = import_mw * DT_HOURS * carbon_factor.carbon_kgco2e_per_mwh

        total_cost += cost_usd
        total_carbon += carbon
        total_import += import_mw * DT_HOURS
        total_export += export_mw * DT_HOURS
        total_available += available_total * DT_HOURS
        total_dispatched += renewable_dispatch * DT_HOURS
        total_curtailment += curtailment_mwh
        total_unserved_critical += critical_r
        total_unserved_noncritical += noncritical_r
        total_dr_shed += dr_shed_mwh
        total_dr_shift += dr_shift_mwh
        total_dr_rebound += dr_rebound_mwh
        total_dr_cost += dr_cost_r
        total_market_revenue += market_revenue_r
        total_nonmarket_valuation += nonmarket_export_valuation_r
        total_market_shortfall_mwh += round(settle.market_shortfall_mwh, 6)
        total_market_shortfall_penalty += market_shortfall_penalty_r
        total_committed_export += settle.committed_export_mw * DT_HOURS
        total_uncommitted_export += settle.uncommitted_export_mw * DT_HOURS
        reserve_shortfall = sum(max(0.0, next(asset for asset in portfolio.assets if asset.asset_id == bess_id).reserve_mwh - energy) for bess_id, energy in energies.items())
        total_reserve_shortfall += reserve_shortfall

        slot_states.append(TwinSlotState(
            replay_id=replay_id,
            slot_index=slot,
            timestamp_utc=portfolio.fixture_start_utc + dt.timedelta(minutes=slot * 15),
            renewable_available_mw=round(available_total, 6),
            renewable_dispatch_mw=round(renewable_dispatch, 6),
            demand_mw=round(demand_total_mw, 6),
            import_mw=round(import_mw, 6),
            export_mw=round(export_mw, 6),
            flow_mw=round(flow, 6),
            bess_energy_mwh={key: round(value, 6) for key, value in sorted(energies.items())},
            bess_soc_fraction={asset.asset_id: round(energies[asset.asset_id] / asset.capacity_mwh, 6) for asset in portfolio.assets_of("bess")},
            reserve_shortfall_mwh=round(reserve_shortfall, 6),
            served_mwh=served_r,
            critical_unserved_mwh=critical_r,
            noncritical_unserved_mwh=noncritical_r,
            curtailment_mwh=round(curtailment_mwh, 6),
            grid_import_cost_usd=grid_import_cost_r,
            market_revenue_usd=market_revenue_r,
            nonmarket_export_valuation_usd=nonmarket_export_valuation_r,
            market_shortfall_mwh=round(settle.market_shortfall_mwh, 6),
            market_shortfall_penalty_usd=market_shortfall_penalty_r,
            committed_export_mw=round(settle.committed_export_mw, 6),
            uncommitted_export_mw=round(settle.uncommitted_export_mw, 6),
            dr_shed_mwh=round(dr_shed_mwh, 6),
            dr_shift_mwh=round(dr_shift_mwh, 6),
            dr_rebound_mwh=round(dr_rebound_mwh, 6),
            dr_cost_usd=dr_cost_r,
            cost_usd=cost_usd,
            carbon_kgco2e=round(carbon, 6),
            event_ids=active_ids,
            action_applied=action_applied,
            proposal_id=proposal_id if action_applied else None,
            decision_id=decision_id if action_applied else None,
            provenance=_prov(replay_id, str(slot), approval.record_id if approval else "no-approval"),
        ))
        if action_applied:
            previous_flow = flow

    if contracted_ids and contracted_denom > 0:
        contracted_fraction_status = "available"
        contracted_fraction_reason = None
        contracted_fraction_value = round(contracted_numer / contracted_denom, 6)
    else:
        contracted_fraction_status = "unavailable"
        contracted_fraction_reason = "no contracted flexible load in portfolio"
        contracted_fraction_value = None

    metric_values = [
        build_metric("total_import_mwh", round(total_import, 6)),
        build_metric("total_export_mwh", round(total_export, 6)),
        build_metric("net_cost_usd", round(total_cost, 6)),
        build_metric("market_revenue_usd", round(total_market_revenue, 6)),
        build_metric("nonmarket_export_valuation_usd", round(total_nonmarket_valuation, 6)),
        build_metric("market_shortfall_mwh", round(total_market_shortfall_mwh, 6)),
        build_metric("market_shortfall_penalty_usd", round(total_market_shortfall_penalty, 6)),
        build_metric("committed_export_mwh", round(total_committed_export, 6)),
        build_metric("uncommitted_export_mwh", round(total_uncommitted_export, 6)),
        build_metric("renewable_utilization_fraction", round(total_dispatched / total_available, 6) if total_available else None, status="available" if total_available else "unavailable", reason=None if total_available else "no renewable availability"),
        build_metric("renewable_curtailment_mwh", round(total_curtailment, 6)),
        build_metric("carbon_kgco2e", round(total_carbon, 6)),
        build_metric("carbon_factor_kgco2e_per_mwh", round(carbon_factor.carbon_kgco2e_per_mwh, 6)),
        build_metric("critical_unserved_mwh", round(total_unserved_critical, 6)),
        build_metric("noncritical_unserved_mwh", round(total_unserved_noncritical, 6)),
        build_metric("validator_predicted_critical_unserved_mwh", round(total_validator_predicted_critical, 6)),
        build_metric("reserve_shortfall_mwh", round(total_reserve_shortfall, 6)),
        build_metric("bess_throughput_mwh", round(throughput, 6)),
        build_metric("dr_shed_mwh", round(total_dr_shed, 6)),
        build_metric("dr_shift_mwh", round(total_dr_shift, 6)),
        build_metric("dr_rebound_mwh", round(total_dr_rebound, 6)),
        build_metric("dr_cost_usd", round(total_dr_cost, 6)),
        build_metric("noncritical_reduction_mwh", round(total_noncritical_reduction, 6)),
        build_metric("contracted_service_fraction", contracted_fraction_value, status=contracted_fraction_status, reason=contracted_fraction_reason),
        build_metric("planned_fallback_count", planned_fallback_count),
        build_metric("applied_fallback_count", applied_fallback_count),
        build_metric("safety_filter_interventions", safety_interventions),
        build_metric("unapplied_slots_with_unserved_count", unapplied_with_unserved),
        build_metric("approval_events", sum(1 for slot in applied_slots if slot in approvals)),
        build_metric("trace_complete", 1 if len(slot_states) == SLOT_COUNT else 0),
    ]
    metrics = Metrics(
        replay_id=replay_id,
        fixture_set_hash=portfolio.fixture_set_hash or "0" * 64,
        realized_path_id=path.scenario_id,
        values=metric_values,
        trace_complete=len(slot_states) == SLOT_COUNT,
        provenance=_prov(replay_id, path.path_sha256 or path.scenario_id, carbon_factor.provenance.content_sha256 or carbon_factor.factor_version),
        carbon_factor_version=carbon_factor.factor_version,
        carbon_factor_digest=carbon_factor.provenance.content_sha256 or sha256_hex(carbon_factor),
    )
    return TwinResult(slot_states=slot_states, metrics=metrics, final_energy_mwh=energies, final_flow_mw=previous_flow)
