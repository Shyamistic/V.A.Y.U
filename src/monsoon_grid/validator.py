"""Independent deterministic safety validator."""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

from .canonical import sha256_hex
from .config import BESS_NUMERIC_TOLERANCE_MWH, DT_HOURS
from .contracts import (
    ActionProposal,
    BessSpec,
    EventRecord,
    MarketIntent,
    PortfolioFixture,
    QualityDecision,
    SafetyDecision,
    ScenarioPath,
    Provenance,
    Violation,
    market_delivery_due,
)
from .dispatch import available_generation, demand_by_load, effective_tie_capacity, next_energy, partition_unserved, slot_supply_mw

VALIDATOR_VERSION = "validator-independent-v2"


def _prov(*refs: str) -> Provenance:
    return Provenance(label="derived", source_id="validator", source_version=VALIDATOR_VERSION, input_refs=list(refs) or ["validator"])


def _violation(code: str, severity: str, path: str, constraint: str, measured, limit, unit: str | None, recoverable: bool, proposal_id: str) -> Violation:
    if isinstance(measured, float):
        measured = round(measured, 6)
    if isinstance(limit, float):
        limit = round(limit, 6)
    return Violation(code=code, severity=severity, field_path=path, constraint=constraint, measured_value=measured, limit_value=limit, unit=unit, recoverable=recoverable, provenance=_prov(proposal_id))


def _check_nonnegative(mapping: Mapping[str, Sequence[float]], field: str, proposal_id: str, violations: list[Violation]) -> None:
    for key, values in mapping.items():
        if not values or values[0] < -BESS_NUMERIC_TOLERANCE_MWH:
            violations.append(_violation("NEGATIVE_ACTION", "fatal", f"{field}.{key}", "all action values must be non-negative", values[0] if values else None, 0.0, "MW", True, proposal_id))


def _check_roster(mapping: Mapping[str, Sequence[float]], expected: set[str], field: str, proposal_id: str, violations: list[Violation]) -> None:
    for key in sorted(set(mapping) - expected):
        violations.append(_violation("UNKNOWN_ASSET", "fatal", f"{field}.{key}", "action ID must be declared in the exact roster", key, "declared", None, False, proposal_id))
    for key in sorted(expected - set(mapping)):
        violations.append(_violation("MISSING_ASSET_ACTION", "fatal", f"{field}.{key}", "every declared asset/load requires exactly one action", None, key, None, False, proposal_id))


def _quality_maps(quality_decisions: Iterable[QualityDecision]) -> tuple[bool, set[str]]:
    blocked = False
    no_dr: set[str] = set()
    for decision in quality_decisions:
        if decision.blocks_action and decision.signal_class == "critical":
            blocked = True
        if decision.signal_class == "noncritical" and decision.quality != "fresh":
            if decision.signal in {"flexible_noncritical_load", "shiftable_process", "deferrable_aux", "flexible_load"}:
                no_dr.add(decision.signal)
    return blocked, no_dr


def validate_proposal(
    portfolio: PortfolioFixture,
    path: ScenarioPath,
    events: Iterable[EventRecord],
    proposal: ActionProposal,
    *,
    replay_id: str,
    slot_index: int,
    energy_state: dict[str, float] | None = None,
    previous_flow_mw: float = 0.0,
    quality_blocked: bool = False,
    quality_decisions: Iterable[QualityDecision] = (),
    dr_history: Mapping[str, Mapping[str, Sequence[float]]] | None = None,
) -> SafetyDecision:
    events = list(events)
    quality_decisions = list(quality_decisions)
    quality_is_blocked, no_dr_signals = _quality_maps(quality_decisions)
    quality_is_blocked = quality_is_blocked or quality_blocked
    violations: list[Violation] = []
    energies = dict(energy_state or {asset.asset_id: asset.initial_energy_mwh for asset in portfolio.assets if isinstance(asset, BessSpec)})
    proposal_id = proposal.proposal_id
    if proposal.replay_id != replay_id:
        violations.append(_violation("REPLAY_ID_MISMATCH", "fatal", "proposal.replay_id", "proposal replay must equal request replay", proposal.replay_id, replay_id, None, False, proposal_id))
    if proposal.commit_slot != slot_index:
        violations.append(_violation("SLOT_CONFLICT", "fatal", "proposal.commit_slot", "proposal commits only the expected slot", proposal.commit_slot, slot_index, "count", False, proposal_id))
    if quality_is_blocked:
        violations.append(_violation("DATA_QUALITY_BLOCKED", "fatal", "quality", "critical quality gate must pass or provide a validated fallback", "blocked", "validated", None, True, proposal_id))

    renewable_assets = portfolio.assets_of("solar_farm") + portfolio.assets_of("wind_farm")
    bess_assets = portfolio.assets_of("bess")
    load_assets = portfolio.assets_of("load_class")
    _check_roster(proposal.renewable_dispatch_mw, {asset.asset_id for asset in renewable_assets}, "renewable_dispatch_mw", proposal_id, violations)
    _check_roster(proposal.bess_charge_mw, {asset.asset_id for asset in bess_assets}, "bess_charge_mw", proposal_id, violations)
    _check_roster(proposal.bess_discharge_mw, {asset.asset_id for asset in bess_assets}, "bess_discharge_mw", proposal_id, violations)
    _check_roster(proposal.shed_mw, {asset.asset_id for asset in load_assets}, "shed_mw", proposal_id, violations)
    _check_roster(proposal.shift_down_mw, {asset.asset_id for asset in load_assets}, "shift_down_mw", proposal_id, violations)
    _check_roster(proposal.shift_up_mw, {asset.asset_id for asset in load_assets}, "shift_up_mw", proposal_id, violations)
    _check_roster(proposal.rebound_mw, {asset.asset_id for asset in load_assets}, "rebound_mw", proposal_id, violations)
    for mapping, field in (
        (proposal.renewable_dispatch_mw, "renewable_dispatch_mw"),
        (proposal.bess_charge_mw, "bess_charge_mw"),
        (proposal.bess_discharge_mw, "bess_discharge_mw"),
        (proposal.shed_mw, "shed_mw"),
        (proposal.shift_down_mw, "shift_down_mw"),
        (proposal.shift_up_mw, "shift_up_mw"),
        (proposal.rebound_mw, "rebound_mw"),
    ):
        _check_nonnegative(mapping, field, proposal_id, violations)
    if proposal.grid_import_mw[0] < -BESS_NUMERIC_TOLERANCE_MWH or proposal.grid_export_mw[0] < -BESS_NUMERIC_TOLERANCE_MWH:
        violations.append(_violation("NEGATIVE_GRID_ACTION", "fatal", "grid", "import and export must be non-negative", min(proposal.grid_import_mw[0], proposal.grid_export_mw[0]), 0.0, "MW", True, proposal_id))

    available = available_generation(portfolio, path, events, slot_index)
    for asset in renewable_assets:
        value = proposal.renewable_dispatch_mw.get(asset.asset_id, [0.0])[0]
        if value > available[asset.asset_id] + BESS_NUMERIC_TOLERANCE_MWH:
            violations.append(_violation("RENEWABLE_AVAILABILITY", "fatal", f"renewable_dispatch_mw.{asset.asset_id}", "dispatch <= scenario/event availability", value, available[asset.asset_id], "MW", True, proposal_id))

    for bess in sorted(bess_assets, key=lambda item: item.asset_id):
        charge = proposal.bess_charge_mw.get(bess.asset_id, [0.0])[0]
        discharge = proposal.bess_discharge_mw.get(bess.asset_id, [0.0])[0]
        energy = energies.get(bess.asset_id, bess.initial_energy_mwh)
        if charge > BESS_NUMERIC_TOLERANCE_MWH and discharge > BESS_NUMERIC_TOLERANCE_MWH:
            violations.append(_violation("BESS_SIMULTANEOUS_CHARGE_DISCHARGE", "fatal", f"bess.{bess.asset_id}", "charge and discharge are mutually exclusive", charge, 0.0, "MW", True, proposal_id))
        if charge > bess.charge_rate_mw + BESS_NUMERIC_TOLERANCE_MWH or discharge > bess.discharge_rate_mw + BESS_NUMERIC_TOLERANCE_MWH:
            violations.append(_violation("BESS_RATE", "fatal", f"bess.{bess.asset_id}", "power <= declared rate", max(charge, discharge), max(bess.charge_rate_mw, bess.discharge_rate_mw), "MW", True, proposal_id))
        unavailable = bess.outage.status == "unavailable" or any(event.event_type == "bess_outage" and bess.asset_id in event.affected_ids and event.start_slot <= slot_index <= event.end_slot for event in events)
        if unavailable and (charge > BESS_NUMERIC_TOLERANCE_MWH or discharge > BESS_NUMERIC_TOLERANCE_MWH):
            violations.append(_violation("ASSET_UNAVAILABLE", "fatal", f"bess.{bess.asset_id}", "unavailable BESS must have zero dispatch", max(charge, discharge), 0.0, "MW", True, proposal_id))
        try:
            after = next_energy(energy, charge, discharge, bess)
        except ValueError:
            after = energy
        reserve = bess.reserve_mwh
        unsafe_events = [event for event in events if event.event_type == "unsafe_rejection" and event.start_slot == slot_index and bess.asset_id in event.affected_ids]
        if unsafe_events and discharge >= 8.0 - BESS_NUMERIC_TOLERANCE_MWH:
            reserve = max(reserve, float(unsafe_events[0].details.get("reserve_mwh", reserve)))
        if after < reserve - BESS_NUMERIC_TOLERANCE_MWH:
            violations.append(_violation("RESERVE_SHORTFALL", "fatal", f"bess.{bess.asset_id}.energy_mwh", "next energy >= reserve", after, reserve, "MWh", False, proposal_id))
        if slot_index == portfolio.slot_count - 1 and after < bess.terminal_target_mwh - BESS_NUMERIC_TOLERANCE_MWH:
            violations.append(_violation("TERMINAL_TARGET", "fatal", f"bess.{bess.asset_id}.energy_mwh", "final energy >= terminal target", after, bess.terminal_target_mwh, "MWh", False, proposal_id))
        if after < -BESS_NUMERIC_TOLERANCE_MWH or after > bess.capacity_mwh + BESS_NUMERIC_TOLERANCE_MWH:
            violations.append(_violation("BESS_ENERGY_BOUNDS", "fatal", f"bess.{bess.asset_id}.energy_mwh", "0 <= next energy <= capacity", after, bess.capacity_mwh, "MWh", True, proposal_id))

    grid = next(asset for asset in portfolio.assets if asset.kind == "grid_interface")
    tie = next(asset for asset in portfolio.assets if asset.kind == "tie_line")
    grid_import = proposal.grid_import_mw[0]
    grid_export = proposal.grid_export_mw[0]
    flow = grid_import - grid_export
    tie_limit = effective_tie_capacity(portfolio, events, slot_index)
    if grid_import > grid.import_limit_mw + BESS_NUMERIC_TOLERANCE_MWH or grid_export > grid.export_limit_mw + BESS_NUMERIC_TOLERANCE_MWH:
        violations.append(_violation("GRID_LIMIT", "fatal", "grid", "directional grid limits", max(grid_import, grid_export), max(grid.import_limit_mw, grid.export_limit_mw), "MW", True, proposal_id))
    if grid_import > BESS_NUMERIC_TOLERANCE_MWH and grid_export > BESS_NUMERIC_TOLERANCE_MWH:
        violations.append(_violation("GRID_SIMULTANEOUS_IMPORT_EXPORT", "fatal", "grid", "import and export are mutually exclusive", min(grid_import, grid_export), 0.0, "MW", True, proposal_id))
    if abs(flow) > tie_limit + BESS_NUMERIC_TOLERANCE_MWH:
        violations.append(_violation("TIE_LINE_LIMIT", "fatal", "grid.flow_mw", "flow = import - export within effective tie capacity", abs(flow), tie_limit, "MW", True, proposal_id))
    if abs(flow - previous_flow_mw) > min(grid.ramp_mw_per_slot, tie.ramp_mw_per_slot) + BESS_NUMERIC_TOLERANCE_MWH:
        violations.append(_violation("TIE_LINE_RAMP", "fatal", "grid.flow_mw", "flow ramp per slot", abs(flow - previous_flow_mw), min(grid.ramp_mw_per_slot, tie.ramp_mw_per_slot), "MW", True, proposal_id))

    loads = demand_by_load(portfolio, path, events, slot_index)
    history = dr_history or {}
    for load in load_assets:
        shed = proposal.shed_mw.get(load.asset_id, [0.0])[0]
        down = proposal.shift_down_mw.get(load.asset_id, [0.0])[0]
        up = proposal.shift_up_mw.get(load.asset_id, [0.0])[0]
        rebound = proposal.rebound_mw.get(load.asset_id, [0.0])[0]
        stale_no_dr = load.asset_id in no_dr_signals or ("flexible_noncritical_load" in no_dr_signals and not load.critical)
        if load.critical and any(value > BESS_NUMERIC_TOLERANCE_MWH for value in (shed, down, up, rebound)):
            violations.append(_violation("CRITICAL_LOAD_SHED", "fatal", f"load.{load.asset_id}", "critical loads never shed or shifted", max(shed, down, up, rebound), 0.0, "MW", False, proposal_id))
        if load.opt_out and any(value > BESS_NUMERIC_TOLERANCE_MWH for value in (shed, down, up, rebound)):
            violations.append(_violation("DR_OPT_OUT", "fatal", f"load.{load.asset_id}", "opt-out load cannot be altered", max(shed, down, up, rebound), 0.0, "MW", False, proposal_id))
        if stale_no_dr and any(value > BESS_NUMERIC_TOLERANCE_MWH for value in (shed, down, up, rebound)):
            violations.append(_violation("DR_QUALITY_BLOCKED", "fatal", f"load.{load.asset_id}", "stale or invalid DR telemetry disables optional DR", max(shed, down, up, rebound), 0.0, "MW", True, proposal_id))
        if shed > load.max_shed_mw + BESS_NUMERIC_TOLERANCE_MWH or down > load.max_shift_mw + BESS_NUMERIC_TOLERANCE_MWH or up > load.max_shift_mw + BESS_NUMERIC_TOLERANCE_MWH:
            violations.append(_violation("DR_LIMIT", "fatal", f"load.{load.asset_id}", "DR values within contracted scalar limits", max(shed, down, up), max(load.max_shed_mw, load.max_shift_mw), "MW", True, proposal_id))
        if abs(down - up) > BESS_NUMERIC_TOLERANCE_MWH:
            violations.append(_violation("DR_CONSERVATION", "fatal", f"load.{load.asset_id}", "shift energy is conserved by the proposal deadline", down * DT_HOURS - up * DT_HOURS, 0.0, "MWh", True, proposal_id))
        previous_rebound = sum(float(value) for value in history.get(load.asset_id, {}).get("rebound", ())) * DT_HOURS
        if rebound > load.rebound_rate_mw + BESS_NUMERIC_TOLERANCE_MWH or (rebound > BESS_NUMERIC_TOLERANCE_MWH and slot_index not in load.rebound_window_slots):
            violations.append(_violation("DR_REBOUND", "fatal", f"load.{load.asset_id}.rebound_mw", "rebound is bounded and windowed", rebound, load.rebound_rate_mw, "MW", True, proposal_id))
        if previous_rebound + rebound * DT_HOURS > load.contracted_rebound_mwh + BESS_NUMERIC_TOLERANCE_MWH:
            violations.append(_violation("DR_REBOUND_CONTRACT", "fatal", f"load.{load.asset_id}.rebound_mw", "rebound energy stays within contract", previous_rebound + rebound * DT_HOURS, load.contracted_rebound_mwh, "MWh", True, proposal_id))

    known_market_ids = {intent.market_id for intent in portfolio.market_intents}
    for intent in proposal.market_intents:
        if intent.market_id not in known_market_ids:
            violations.append(_violation("UNKNOWN_MARKET_INTENT", "fatal", f"market_intents.{intent.market_id}", "market intent must be declared", intent.market_id, "declared", None, False, proposal_id))
        elif not market_delivery_due(intent, delivery_slot=slot_index):
            violations.append(_violation("MARKET_DELIVERY_OUT_OF_WINDOW", "fatal", f"market_intents.{intent.market_id}", "market intent must satisfy delivery, approval, and settlement predicates", slot_index, "open", "count", True, proposal_id))
    if len({intent.market_id for intent in proposal.market_intents}) != len(proposal.market_intents):
        violations.append(_violation("DUPLICATE_MARKET_INTENT", "fatal", "market_intents", "market IDs must be unique", "duplicate", "unique", None, False, proposal_id))

    dispatched = sum(proposal.renewable_dispatch_mw.get(asset.asset_id, [0.0])[0] for asset in renewable_assets)
    discharged = sum(proposal.bess_discharge_mw.get(asset.asset_id, [0.0])[0] for asset in bess_assets)
    charged = sum(proposal.bess_charge_mw.get(asset.asset_id, [0.0])[0] for asset in bess_assets)
    shed_total = sum(proposal.shed_mw.get(asset.asset_id, [0.0])[0] for asset in load_assets)
    shift_down_total = sum(proposal.shift_down_mw.get(asset.asset_id, [0.0])[0] for asset in load_assets)
    shift_up_total = sum(proposal.shift_up_mw.get(asset.asset_id, [0.0])[0] for asset in load_assets)
    rebound_total = sum(proposal.rebound_mw.get(asset.asset_id, [0.0])[0] for asset in load_assets)
    demand_total = sum(loads.values())
    balance_residual_mw = slot_supply_mw(
        renewable_dispatch_mw=dispatched,
        discharge_mw=discharged,
        import_mw=grid_import,
        shed_mw=shed_total,
        shift_down_mw=shift_down_total,
        charge_mw=charged,
        export_mw=grid_export,
        shift_up_mw=shift_up_total,
        rebound_mw=rebound_total,
    ) - demand_total
    total_unserved_mwh = max(0.0, -balance_residual_mw) * DT_HOURS
    critical_unserved, _ = partition_unserved(total_unserved_mwh, loads, load_assets)
    if abs(balance_residual_mw) > BESS_NUMERIC_TOLERANCE_MWH:
        violations.append(_violation("ENERGY_BALANCE", "fatal" if critical_unserved > BESS_NUMERIC_TOLERANCE_MWH else "warning", "proposal", "per-slot energy balance", balance_residual_mw, 0.0, "MW", critical_unserved <= BESS_NUMERIC_TOLERANCE_MWH, proposal_id))

    reserve_shortfall = 0.0
    for violation in violations:
        if violation.code in {"RESERVE_SHORTFALL", "TERMINAL_TARGET"} and isinstance(violation.measured_value, (int, float)) and isinstance(violation.limit_value, (int, float)):
            reserve_shortfall += max(0.0, float(violation.limit_value) - float(violation.measured_value))
    fatal = any(violation.severity == "fatal" for violation in violations)
    escalated = quality_is_blocked or critical_unserved > BESS_NUMERIC_TOLERANCE_MWH or reserve_shortfall > BESS_NUMERIC_TOLERANCE_MWH
    status = "ESCALATED_NO_SAFE_ACTION" if escalated else ("REJECTED" if fatal else "ACCEPTED")
    return SafetyDecision(
        decision_id=proposal.decision_id,
        replay_id=replay_id,
        slot_index=slot_index,
        status=status,
        approved=False,
        critical_unserved_mwh=round(critical_unserved, 6),
        reserve_shortfall_mwh=round(reserve_shortfall, 6),
        violations=violations,
        recovery_options=["request_replan", "preserve_critical_load", "escalate_to_operator"] if status != "ACCEPTED" else [],
        validator_version=VALIDATOR_VERSION,
        proposal_digest=sha256_hex(proposal),
        provenance=_prov(proposal_id, path.path_sha256 or path.scenario_id),
    )
