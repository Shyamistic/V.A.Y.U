"""Versioned, deterministic conservative fallback rule table."""

from __future__ import annotations

import datetime as dt
import hashlib
from time import monotonic
from typing import Iterable

from .canonical import sha256_hex
from .config import FALLBACK_ALGORITHM_VERSION, BESS_NUMERIC_TOLERANCE_MWH, DT_HOURS
from .contracts import ActionProposal, EventRecord, PolicyMode, PortfolioFixture, Provenance, ScenarioPath, SolverResult
from .dispatch import available_generation, default_energy, demand_by_load, effective_tie_capacity, safe_bess_discharge

FALLBACK_ALGORITHM_HASH = hashlib.sha256(FALLBACK_ALGORITHM_VERSION.encode("utf-8")).hexdigest()


def _prov(*refs: str) -> Provenance:
    return Provenance(label="derived", source_id="fallback", source_version=FALLBACK_ALGORITHM_VERSION, input_refs=list(refs) or ["fallback"])


def fallback_plan(
    portfolio: PortfolioFixture,
    path: ScenarioPath,
    events: Iterable[EventRecord],
    *,
    replay_id: str,
    slot_index: int,
    policy_mode: PolicyMode,
    energy_state: dict[str, float] | None = None,
    previous_flow_mw: float = 0.0,
    reason: str = "SOLVER_UNAVAILABLE",
    deadline: float | None = None,
    dr_allowed_load_ids: set[str] | None = None,
) -> tuple[ActionProposal, SolverResult, list[str]]:
    """Apply the fixed priority table and return one advisory slot.

    1. Apply quality/event decisions and mark unavailable assets.
    2. Serve critical load and preserve each BESS reserve/terminal target.
    3. Discharge BESSs by ascending roster ID and remaining energy margin.
    4. Import up to grid/tie/ramp limits, then export/curtail in roster order.
    5. Apply only enrolled noncritical shed/shift; rebound is separate and bounded.
    6. Return a proposal for independent validation; no action is consumed here.
    """
    start = monotonic()
    events = list(events)
    energies = dict(default_energy(portfolio) if energy_state is None else energy_state)
    available = available_generation(portfolio, path, events, slot_index)
    loads = demand_by_load(portfolio, path, events, slot_index)
    renewable_dispatch = dict(available)
    charge = {asset.asset_id: 0.0 for asset in portfolio.assets_of("bess")}
    discharge = {asset.asset_id: 0.0 for asset in portfolio.assets_of("bess")}
    shed = {asset.asset_id: 0.0 for asset in portfolio.assets_of("load_class")}
    shift_down = {asset.asset_id: 0.0 for asset in portfolio.assets_of("load_class")}
    shift_up = {asset.asset_id: 0.0 for asset in portfolio.assets_of("load_class")}
    rebound = {asset.asset_id: 0.0 for asset in portfolio.assets_of("load_class")}
    total_demand = sum(loads.values())
    total_generation = sum(renewable_dispatch.values())
    remaining = max(0.0, total_demand - total_generation)

    # Critical service is considered before any flexible action. BESS tie-breaks
    # are roster ID then available energy margin, both deterministic.
    bess_assets = sorted(portfolio.assets_of("bess"), key=lambda asset: (asset.asset_id, max(0.0, energies.get(asset.asset_id, asset.initial_energy_mwh) - asset.reserve_mwh)))
    for bess in bess_assets:
        requested = remaining
        allowed = safe_bess_discharge(energies.get(bess.asset_id, bess.initial_energy_mwh), bess, requested, available=bess.outage.status != "unavailable" and bess.enabled and not any(event.event_type == "bess_outage" and bess.asset_id in event.affected_ids and event.start_slot <= slot_index <= event.end_slot for event in events))
        discharge[bess.asset_id] = allowed
        remaining = max(0.0, remaining - allowed)

    grid = next(asset for asset in portfolio.assets if asset.kind == "grid_interface")
    tie_limit = effective_tie_capacity(portfolio, events, slot_index)
    ramp_limit = min(grid.ramp_mw_per_slot, next(asset for asset in portfolio.assets if asset.kind == "tie_line").ramp_mw_per_slot)
    import_limit = min(grid.import_limit_mw, tie_limit, max(0.0, previous_flow_mw + ramp_limit))
    grid_import = min(remaining, import_limit)
    remaining = max(0.0, remaining - grid_import)

    # Noncritical shed is last-resort and never applied to critical/opt-out loads.
    allowed_dr = dr_allowed_load_ids if dr_allowed_load_ids is not None else {asset.asset_id for asset in portfolio.assets_of("load_class") if asset.opt_in and not asset.opt_out and not asset.critical}
    for load in sorted(portfolio.assets_of("load_class"), key=lambda asset: asset.asset_id):
        if remaining <= BESS_NUMERIC_TOLERANCE_MWH or load.asset_id not in allowed_dr or load.critical or not load.opt_in or load.opt_out:
            continue
        amount = min(load.max_shed_mw, remaining)
        shed[load.asset_id] = round(amount, 6)
        remaining = max(0.0, remaining - amount)

    excess = max(0.0, total_generation + sum(discharge.values()) + grid_import - total_demand + sum(shed.values()))
    export_limit = min(grid.export_limit_mw, tie_limit, max(0.0, ramp_limit - previous_flow_mw))
    grid_export = min(excess, export_limit)

    # Enforce the signed flow ramp exactly: flow = import - export and the
    # feasible interval is [previous-ramp, previous+ramp].
    flow_min = max(-tie_limit, previous_flow_mw - ramp_limit)
    flow_max = min(tie_limit, previous_flow_mw + ramp_limit)
    current_flow = grid_import - grid_export
    if current_flow > flow_max + BESS_NUMERIC_TOLERANCE_MWH:
        delta = current_flow - flow_max
        if grid_import >= delta:
            grid_import = round(grid_import - delta, 6)
        else:
            grid_export = round(grid_export + (delta - grid_import), 6)
            grid_import = 0.0
    elif current_flow < flow_min - BESS_NUMERIC_TOLERANCE_MWH:
        delta = flow_min - current_flow
        if grid_export >= delta:
            grid_export = round(grid_export - delta, 6)
        else:
            grid_import = round(grid_import + (delta - grid_export), 6)
            grid_export = 0.0

    # Repair the energy balance after the ramp projection without violating
    # reserve: discharge by roster ID for a deficit, curtail by roster ID for a surplus.
    residual = total_generation + sum(discharge.values()) + grid_import + sum(shed.values()) - total_demand - grid_export
    if residual < -BESS_NUMERIC_TOLERANCE_MWH:
        need = -residual
        for bess in bess_assets:
            extra = safe_bess_discharge(energies.get(bess.asset_id, bess.initial_energy_mwh), bess, need - discharge[bess.asset_id], available=bess.outage.status != "unavailable" and bess.enabled)
            discharge[bess.asset_id] = round(discharge[bess.asset_id] + extra, 6)
            need -= extra
            if need <= BESS_NUMERIC_TOLERANCE_MWH:
                break
        if need > BESS_NUMERIC_TOLERANCE_MWH:
            for load in sorted(portfolio.assets_of("load_class"), key=lambda asset: asset.asset_id):
                if load.asset_id not in allowed_dr or load.critical or not load.opt_in or load.opt_out:
                    continue
                extra = min(load.max_shed_mw - shed[load.asset_id], need)
                shed[load.asset_id] = round(shed[load.asset_id] + max(0.0, extra), 6)
                need -= max(0.0, extra)
                if need <= BESS_NUMERIC_TOLERANCE_MWH:
                    break
    elif residual > BESS_NUMERIC_TOLERANCE_MWH:
        excess = residual
        for asset_id in sorted(renewable_dispatch):
            cut = min(renewable_dispatch[asset_id], excess)
            renewable_dispatch[asset_id] = round(renewable_dispatch[asset_id] - cut, 6)
            excess -= cut
            if excess <= BESS_NUMERIC_TOLERANCE_MWH:
                break

    provenance = _prov(replay_id, path.path_sha256 or path.scenario_id)
    solver = SolverResult(
        status="STDLIB_FALLBACK",
        solver_name="fallback-rule-table",
        solver_version=FALLBACK_ALGORITHM_VERSION,
        budget_seconds=0.0,
        objective_values=[round(grid_import, 6), round(sum(discharge.values()), 6), round(excess, 6)],
        stage_optima=[],
        message=reason,
        fallback_reason=reason,
        method_hash=FALLBACK_ALGORITHM_HASH,
        provenance=provenance,
    )
    expiry = portfolio.fixture_start_utc + dt.timedelta(minutes=(slot_index + 1) * 15)
    proposal = ActionProposal(
        proposal_id=f"{replay_id}:proposal:{slot_index}",
        replay_id=replay_id,
        decision_id=f"{replay_id}:decision:{slot_index}",
        commit_slot=slot_index,
        renewable_dispatch_mw={key: [value] for key, value in sorted(renewable_dispatch.items())},
        bess_charge_mw={key: [value] for key, value in sorted(charge.items())},
        bess_discharge_mw={key: [value] for key, value in sorted(discharge.items())},
        grid_import_mw=[round(grid_import, 6)],
        grid_export_mw=[round(grid_export, 6)],
        shed_mw={key: [value] for key, value in sorted(shed.items())},
        shift_down_mw={key: [value] for key, value in sorted(shift_down.items())},
        shift_up_mw={key: [value] for key, value in sorted(shift_up.items())},
        rebound_mw={key: [value] for key, value in sorted(rebound.items())},
        market_intents=[],
        policy_mode=policy_mode,
        solver_result=solver,
        provenance=provenance,
        expires_at=expiry,
    )
    return proposal, solver, [
        "quality_decisions_applied",
        "critical_load_priority",
        "reserve_preserved",
        "bess_roster_order",
        "grid_tie_limit_applied",
        "renewable_roster_curtailment",
        "noncritical_dr_last_resort",
        "proposal_not_consumed_before_validation",
    ]
