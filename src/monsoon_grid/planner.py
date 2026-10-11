"""Scenario-aware lexicographic MILP planner with an explicit fallback boundary."""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from time import monotonic
from typing import Iterable, Mapping, Sequence

from .assumptions import CarbonFactorAssumption, load_carbon_factor, load_flexibility_costs, load_market_settlement
from .canonical import sha256_hex
from .config import (
    BESS_NUMERIC_TOLERANCE_MWH,
    DEFAULT_PER_REQUEST_BUDGET_SECONDS,
    DEFAULT_PER_SLOT_BUDGET_SECONDS,
    DT_HOURS,
    POLICY_OBJECTIVES,
)
from .contracts import (
    ActionProposal,
    BessSpec,
    EventRecord,
    PlanResult,
    PlanningTrace,
    PolicyMode,
    PortfolioFixture,
    Provenance,
    ScenarioPath,
    SolverResult,
    StageOptimum,
    ToolCall,
)
from .dispatch import available_generation, demand_by_load, effective_tie_capacity, in_maintenance
from .fallback import fallback_plan
from .contracts import market_delivery_due
from .equations import slot_cost_rates
from .market import committed_export_mw


PLANNER_METHOD_VERSION = "scenario-milp-lexicographic-v2"
PLANNER_METHOD_HASH = hashlib.sha256(PLANNER_METHOD_VERSION.encode("utf-8")).hexdigest()


def _prov(*refs: str) -> Provenance:
    return Provenance(label="derived", source_id="planner", source_version=PLANNER_METHOD_VERSION, input_refs=list(refs) or ["planner"])


@dataclass
class _MilpModel:
    names: list[str]
    lower: list[float]
    upper: list[float]
    integrality: list[int]
    rows: list[tuple[dict[int, float], float, float]]
    objectives: dict[str, list[float]]
    scenario_indexes: dict[str, dict[str, int]]
    common_indexes: dict[str, dict[str, int]]

    def add_variable(self, name: str, *, lower: float = 0.0, upper: float = float("inf"), binary: bool = False) -> int:
        index = len(self.names)
        self.names.append(name)
        self.lower.append(lower)
        self.upper.append(upper)
        self.integrality.append(1 if binary else 0)
        return index

    def add_row(self, coefficients: Mapping[int, float], lower: float = float("-inf"), upper: float = float("inf")) -> None:
        self.rows.append((dict(coefficients), lower, upper))

    def objective(self, name: str) -> list[float]:
        values = [0.0] * len(self.names)
        for index, coefficient in self._objective_terms(name):
            values[index] += coefficient
        return values

    def _objective_terms(self, name: str):
        # Objective expressions are assembled in _build_model and stored as
        # dense vectors.  This method exists only to keep objective() readable.
        vector = self.objectives.get(name)
        if vector is None:
            raise KeyError(name)
        return enumerate(vector)


def _put(vector: list[float], index: int, value: float) -> None:
    vector[index] += float(value)


def _build_model(
    portfolio: PortfolioFixture,
    paths: Sequence[ScenarioPath],
    events: Sequence[EventRecord],
    *,
    slot_index: int,
    energy_state: Mapping[str, float] | None,
    previous_flow_mw: float,
    dr_allowed_load_ids: set[str] | None,
    carbon_factor: CarbonFactorAssumption,
) -> tuple[_MilpModel, dict[str, int]]:
    """Build the complete one-slot robust model.

    Renewable dispatch, demand, grid flow, DR, unserved energy, and prices are
    scenario-indexed. BESS actions are shared across paths, so the chosen action
    is robust to the five supplied scenario paths while the realized path is
    emitted into the one-slot proposal.
    """
    model = _MilpModel([], [], [], [], [], {}, {}, {})
    _settlement = load_market_settlement()
    _flexibility = load_flexibility_costs()
    renewable_assets = sorted([*portfolio.assets_of("solar_farm"), *portfolio.assets_of("wind_farm")], key=lambda item: item.asset_id)
    bess_assets = sorted(portfolio.assets_of("bess"), key=lambda item: item.asset_id)
    load_assets = sorted(portfolio.assets_of("load_class"), key=lambda item: item.asset_id)
    current_energy = dict(energy_state or {asset.asset_id: asset.initial_energy_mwh for asset in bess_assets})
    grid = next(asset for asset in portfolio.assets if asset.kind == "grid_interface")
    tie = next(asset for asset in portfolio.assets if asset.kind == "tie_line")
    effective_tie = effective_tie_capacity(portfolio, events, slot_index)
    flow_ramp = min(grid.ramp_mw_per_slot, tie.ramp_mw_per_slot)
    prior_flow = float(previous_flow_mw)

    common: dict[str, int] = {}
    for bess in bess_assets:
        energy = float(current_energy.get(bess.asset_id, bess.initial_energy_mwh))
        available = bess.enabled and bess.outage.status != "unavailable" and not in_maintenance(bess, slot_index) and not any(
            event.event_type == "bess_outage"
            and bess.asset_id in event.affected_ids
            and event.start_slot <= slot_index <= event.end_slot
            for event in events
        )
        rate_charge = bess.charge_rate_mw if available else 0.0
        rate_discharge = bess.discharge_rate_mw if available else 0.0
        charge = model.add_variable(f"charge:{bess.asset_id}", upper=rate_charge)
        discharge = model.add_variable(f"discharge:{bess.asset_id}", upper=rate_discharge)
        mode = model.add_variable(f"charge_mode:{bess.asset_id}", upper=1.0, binary=True)
        common[f"charge:{bess.asset_id}"] = charge
        common[f"discharge:{bess.asset_id}"] = discharge
        common[f"mode:{bess.asset_id}"] = mode
        model.add_row({charge: 1.0, mode: -rate_charge}, upper=0.0)
        model.add_row({discharge: 1.0, mode: rate_discharge}, upper=rate_discharge)
        transition = {charge: bess.eta_c * DT_HOURS, discharge: -DT_HOURS / bess.eta_d}
        model.add_row(transition, lower=bess.reserve_mwh - energy, upper=bess.capacity_mwh - energy)
        if slot_index == portfolio.slot_count - 1:
            model.add_row(transition, lower=bess.terminal_target_mwh - energy)
    model.common_indexes["bess"] = common

    for scenario_number, path in enumerate(paths):
        indexes: dict[str, int] = {}
        available_generation_mw = available_generation(portfolio, path, events, slot_index)
        loads = demand_by_load(portfolio, path, events, slot_index)
        for asset in renewable_assets:
            indexes[f"renewable:{asset.asset_id}"] = model.add_variable(
                f"renewable:{scenario_number}:{asset.asset_id}", upper=available_generation_mw[asset.asset_id]
            )
        import_index = model.add_variable(f"import:{scenario_number}", upper=grid.import_limit_mw)
        export_index = model.add_variable(f"export:{scenario_number}", upper=grid.export_limit_mw)
        direction = model.add_variable(f"grid_direction:{scenario_number}", upper=1.0, binary=True)
        indexes["grid_import"] = import_index
        indexes["grid_export"] = export_index
        indexes["grid_direction"] = direction
        model.add_row({import_index: 1.0, direction: -grid.import_limit_mw}, upper=0.0)
        model.add_row({export_index: 1.0, direction: grid.export_limit_mw}, upper=grid.export_limit_mw)
        model.add_row({import_index: 1.0, export_index: -1.0}, lower=prior_flow - flow_ramp, upper=prior_flow + flow_ramp)
        model.add_row({import_index: 1.0, export_index: -1.0}, lower=-effective_tie, upper=effective_tie)
        # A4: split the aggregate export into committed/uncommitted and a priced
        # shortfall. The pre-existing hard export floor is removed; the shortfall
        # is a priced soft target so the optimizer can choose.
        committed_due_mw = committed_export_mw(portfolio.market_commitments, slot_index=slot_index)
        export_committed = model.add_variable(f"export_committed:{scenario_number}", upper=max(0.0, committed_due_mw))
        export_uncommitted = model.add_variable(f"export_uncommitted:{scenario_number}", upper=grid.export_limit_mw)
        market_shortfall = model.add_variable(f"market_shortfall:{scenario_number}", upper=max(0.0, committed_due_mw))
        indexes["export_committed"] = export_committed
        indexes["export_uncommitted"] = export_uncommitted
        indexes["market_shortfall"] = market_shortfall
        model.add_row({export_committed: 1.0, export_uncommitted: 1.0, export_index: -1.0}, lower=0.0, upper=0.0)
        model.add_row({export_committed: 1.0, market_shortfall: 1.0}, lower=committed_due_mw, upper=committed_due_mw)

        for load in load_assets:
            dr_allowed = (
                load.asset_id in (dr_allowed_load_ids if dr_allowed_load_ids is not None else {item.asset_id for item in load_assets})
                and load.opt_in
                and not load.opt_out
                and not load.critical
            )
            shed_upper = load.max_shed_mw if dr_allowed else 0.0
            shift_upper = load.max_shift_mw if dr_allowed else 0.0
            rebound_upper = load.rebound_rate_mw if dr_allowed and slot_index in load.rebound_window_slots else 0.0
            # F3: UNPRICED voluntary non-critical reduction, bounded by
            # max_reduce_mw, gated by the same dr_allowed predicate.
            reduce_upper = load.max_reduce_mw if dr_allowed else 0.0
            shed = model.add_variable(f"shed:{scenario_number}:{load.asset_id}", upper=shed_upper)
            shift_down = model.add_variable(f"shift_down:{scenario_number}:{load.asset_id}", upper=shift_upper)
            shift_up = model.add_variable(f"shift_up:{scenario_number}:{load.asset_id}", upper=shift_upper)
            rebound = model.add_variable(f"rebound:{scenario_number}:{load.asset_id}", upper=rebound_upper)
            reduce_ = model.add_variable(f"reduce:{scenario_number}:{load.asset_id}", upper=reduce_upper)
            indexes[f"shed:{load.asset_id}"] = shed
            indexes[f"shift_down:{load.asset_id}"] = shift_down
            indexes[f"shift_up:{load.asset_id}"] = shift_up
            indexes[f"rebound:{load.asset_id}"] = rebound
            indexes[f"reduce:{load.asset_id}"] = reduce_
            # A one-slot proposal cannot defer a shift commitment beyond its
            # horizon. Equal down/up preserves contracted energy in this model.
            model.add_row({shift_down: 1.0, shift_up: -1.0}, lower=0.0, upper=0.0)
            if not dr_allowed:
                model.add_row({shed: 1.0, shift_down: 1.0, shift_up: 1.0, rebound: 1.0, reduce_: 1.0}, lower=0.0, upper=0.0)

        critical_unserved = model.add_variable(f"critical_unserved:{scenario_number}", upper=sum(value for load_id, value in loads.items() if next(item for item in load_assets if item.asset_id == load_id).critical))
        noncritical_unserved = model.add_variable(f"noncritical_unserved:{scenario_number}", upper=sum(value for load_id, value in loads.items() if not next(item for item in load_assets if item.asset_id == load_id).critical))
        indexes["critical_unserved"] = critical_unserved
        indexes["noncritical_unserved"] = noncritical_unserved
        balance: dict[int, float] = {critical_unserved: 1.0, noncritical_unserved: 1.0, import_index: -1.0, export_index: 1.0}
        for asset in renewable_assets:
            balance[indexes[f"renewable:{asset.asset_id}"]] = -1.0
        for bess in bess_assets:
            balance[common[f"discharge:{bess.asset_id}"]] = balance.get(common[f"discharge:{bess.asset_id}"], 0.0) - 1.0
            balance[common[f"charge:{bess.asset_id}"]] = balance.get(common[f"charge:{bess.asset_id}"], 0.0) + 1.0
        for load in load_assets:
            balance[indexes[f"shed:{load.asset_id}"]] = balance.get(indexes[f"shed:{load.asset_id}"], 0.0) - 1.0
            balance[indexes[f"shift_down:{load.asset_id}"]] = balance.get(indexes[f"shift_down:{load.asset_id}"], 0.0) - 1.0
            balance[indexes[f"shift_up:{load.asset_id}"]] = balance.get(indexes[f"shift_up:{load.asset_id}"], 0.0) + 1.0
            balance[indexes[f"rebound:{load.asset_id}"]] = balance.get(indexes[f"rebound:{load.asset_id}"], 0.0) + 1.0
            balance[indexes[f"reduce:{load.asset_id}"]] = balance.get(indexes[f"reduce:{load.asset_id}"], 0.0) - 1.0
        model.add_row(balance, lower=-sum(loads.values()), upper=-sum(loads.values()))
        model.scenario_indexes[str(scenario_number)] = indexes

    # Objective vectors are built after all variables exist.
    for objective_name in {
        "critical_service_margin",
        "required_reliability_tier",
        "same_non_negotiable_safety_tier",
        "reserve_margin",
        "reliability_event_count",
        "net_cost",
        "carbon",
        "curtailment",
        "bess_wear",
        "dr_activation",
    }:
        model.objectives[objective_name] = [0.0] * len(model.names)

    scenario_weight = 1.0 / max(1, len(paths))
    for scenario_number, path in enumerate(paths):
        indexes = model.scenario_indexes[str(scenario_number)]
        available_generation_mw = available_generation(portfolio, path, events, slot_index)
        loads = demand_by_load(portfolio, path, events, slot_index)
        critical = indexes["critical_unserved"]
        noncritical = indexes["noncritical_unserved"]
        for name in ("critical_service_margin", "required_reliability_tier", "same_non_negotiable_safety_tier"):
            _put(model.objectives[name], critical, scenario_weight * DT_HOURS)
        _put(model.objectives["reliability_event_count"], critical, scenario_weight)
        _put(model.objectives["reliability_event_count"], noncritical, scenario_weight)
        price = path.price_usd_per_mwh[slot_index]
        for bess in bess_assets:
            _put(model.objectives["bess_wear"], model.common_indexes["bess"][f"charge:{bess.asset_id}"], scenario_weight * DT_HOURS)
            _put(model.objectives["bess_wear"], model.common_indexes["bess"][f"discharge:{bess.asset_id}"], scenario_weight * DT_HOURS)
        rates = slot_cost_rates(portfolio, path, portfolio.market_commitments, _settlement, _flexibility, slot_index=slot_index)
        _put(model.objectives["net_cost"], indexes["grid_import"], scenario_weight * DT_HOURS * rates.import_usd_per_mwh)
        _put(model.objectives["net_cost"], indexes["export_committed"], -scenario_weight * DT_HOURS * rates.committed_export_usd_per_mwh)
        _put(model.objectives["net_cost"], indexes["export_uncommitted"], -scenario_weight * DT_HOURS * rates.uncommitted_export_usd_per_mwh)
        _put(model.objectives["net_cost"], indexes["market_shortfall"], scenario_weight * DT_HOURS * rates.shortfall_usd_per_mwh)
        _put(model.objectives["carbon"], indexes["grid_import"], scenario_weight * DT_HOURS * carbon_factor.carbon_kgco2e_per_mwh)
        for asset in renewable_assets:
            _put(model.objectives["curtailment"], indexes[f"renewable:{asset.asset_id}"], -scenario_weight * DT_HOURS)
            _put(model.objectives["curtailment"], indexes[f"renewable:{asset.asset_id}"], 0.0)
            # The constant available energy is added when evaluating the final
            # objective; the variable part still minimizes curtailment.
        for load in load_assets:
            _put(model.objectives["dr_activation"], indexes[f"shed:{load.asset_id}"], scenario_weight * DT_HOURS)
            _put(model.objectives["dr_activation"], indexes[f"shift_down:{load.asset_id}"], scenario_weight * DT_HOURS)
            _put(model.objectives["dr_activation"], indexes[f"shift_up:{load.asset_id}"], scenario_weight * DT_HOURS)
            _put(model.objectives["dr_activation"], indexes[f"rebound:{load.asset_id}"], scenario_weight * DT_HOURS)
            # F3: the voluntary reduction carries a dr_activation penalty (so the
            # optimizer only chooses it when it buys a higher-tier objective) but
            # NO net_cost term — it is unpriced reliability relief, not priced DR.
            _put(model.objectives["dr_activation"], indexes[f"reduce:{load.asset_id}"], scenario_weight * DT_HOURS)
            # A2: priced DR enters net_cost. shift is priced on shift_down only
            # (down==up is enforced); rebound carries no cost coefficient.
            _put(model.objectives["net_cost"], indexes[f"shed:{load.asset_id}"], scenario_weight * DT_HOURS * rates.shed_usd_per_mwh.get(load.asset_id, 0.0))
            _put(model.objectives["net_cost"], indexes[f"shift_down:{load.asset_id}"], scenario_weight * DT_HOURS * rates.shift_usd_per_mwh.get(load.asset_id, 0.0))

    # Reserve margin is a real expression: maximize post-action energy by
    # minimizing its negative. The hard reserve constraint still applies.
    for bess in bess_assets:
        charge = model.common_indexes["bess"][f"charge:{bess.asset_id}"]
        discharge = model.common_indexes["bess"][f"discharge:{bess.asset_id}"]
        energy = float(current_energy.get(bess.asset_id, bess.initial_energy_mwh))
        _put(model.objectives["reserve_margin"], charge, -bess.eta_c * DT_HOURS)
        _put(model.objectives["reserve_margin"], discharge, DT_HOURS / bess.eta_d)
        # Constant current energy is intentionally omitted from the solve
        # vector and restored in _objective_values.
        _ = energy
    # A final deterministic objective resolves otherwise equivalent asset-level
    # dispatches without changing any policy-tier optimum.
    model.objectives["deterministic_tiebreak"] = [round((index + 1) * 0.000001, 9) for index in range(len(model.names))]
    return model, common


def _objective_values(model: _MilpModel, x: Sequence[float], portfolio: PortfolioFixture, paths: Sequence[ScenarioPath], events: Sequence[EventRecord], *, slot_index: int, energy_state: Mapping[str, float] | None, carbon_factor: CarbonFactorAssumption) -> dict[str, float]:
    values = {name: sum(coefficient * float(x[index]) for index, coefficient in enumerate(vector)) for name, vector in model.objectives.items()}
    # Curtailment is availability minus dispatch, so add the deterministic
    # availability constant omitted from the linear minimization vector.
    curtailment_constant = 0.0
    for path in paths:
        curtailment_constant += sum(available_generation(portfolio, path, events, slot_index).values()) * DT_HOURS / max(1, len(paths))
    values["curtailment"] += curtailment_constant
    return {name: round(value, 6) for name, value in values.items()}


def _solve_lexicographic(model: _MilpModel, *, objective_order: Sequence[str], deadline: float, request_budget_seconds: float, slot_budget_seconds: float):
    try:
        import numpy as np
        from scipy.optimize import Bounds, LinearConstraint, milp
        import scipy
    except ImportError:
        return None, "UNAVAILABLE", "SciPy/HiGHS unavailable"
    fixed_rows = list(model.rows)
    solution = None
    stages: list[tuple[str, float]] = []
    solver_version = scipy.__version__
    for objective_name in [*objective_order, "deterministic_tiebreak"]:
        remaining = max(0.0, min(deadline - monotonic(), request_budget_seconds, slot_budget_seconds))
        if remaining <= 0.0:
            return None, "TIME_LIMIT", "solver budget exhausted before lexicographic stage"
        matrix = np.zeros((len(fixed_rows), len(model.names)), dtype=float)
        lower = np.zeros(len(fixed_rows), dtype=float)
        upper = np.zeros(len(fixed_rows), dtype=float)
        for row_number, (coefficients, row_lower, row_upper) in enumerate(fixed_rows):
            for index, coefficient in coefficients.items():
                matrix[row_number, index] = coefficient
            lower[row_number] = row_lower
            upper[row_number] = row_upper
        try:
            result = milp(
                c=np.asarray(model.objectives[objective_name], dtype=float),
                integrality=np.asarray(model.integrality, dtype=int),
                bounds=Bounds(np.asarray(model.lower), np.asarray(model.upper)),
                constraints=LinearConstraint(matrix, lower, upper),
                options={"time_limit": max(0.001, remaining), "presolve": True},
            )
        except Exception as exc:
            return None, "ERROR", f"HiGHS scenario model error: {type(exc).__name__}: {exc}"
        if not getattr(result, "success", False) or result.x is None:
            status = "TIME_LIMIT" if getattr(result, "status", None) == 1 else ("INFEASIBLE" if getattr(result, "status", None) == 2 else "ERROR")
            return None, status, str(getattr(result, "message", "scenario model failed"))
        solution = [float(value) for value in result.x]
        value = float(np.dot(np.asarray(model.objectives[objective_name], dtype=float), result.x))
        stages.append((objective_name, round(value, 6)))
        tolerance = max(BESS_NUMERIC_TOLERANCE_MWH, abs(value) * 1e-7)
        fixed_rows.append((dict(enumerate(model.objectives[objective_name])), value - tolerance, value + tolerance))
    return (solution, stages, solver_version), "OPTIMAL", "scenario-aware lexicographic MILP solved"


def _proposal_from_solution(
    portfolio: PortfolioFixture,
    path: ScenarioPath,
    paths: Sequence[ScenarioPath],
    model: _MilpModel,
    solution: Sequence[float],
    *,
    replay_id: str,
    slot_index: int,
    policy_mode: PolicyMode,
    solver: SolverResult,
) -> ActionProposal:
    scenario_number = next((index for index, candidate in enumerate(paths) if candidate.scenario_id == path.scenario_id), 0)
    indexes = model.scenario_indexes[str(scenario_number)]
    renewable = {asset.asset_id: [round(max(0.0, solution[indexes[f"renewable:{asset.asset_id}"]]), 6)] for asset in sorted([*portfolio.assets_of("solar_farm"), *portfolio.assets_of("wind_farm")], key=lambda item: item.asset_id)}
    charge = {asset.asset_id: [round(max(0.0, solution[model.common_indexes["bess"][f"charge:{asset.asset_id}"]]), 6)] for asset in portfolio.assets_of("bess")}
    discharge = {asset.asset_id: [round(max(0.0, solution[model.common_indexes["bess"][f"discharge:{asset.asset_id}"]]), 6)] for asset in portfolio.assets_of("bess")}
    shed = {asset.asset_id: [round(max(0.0, solution[indexes[f"shed:{asset.asset_id}"]]), 6)] for asset in portfolio.assets_of("load_class")}
    shift_down = {asset.asset_id: [round(max(0.0, solution[indexes[f"shift_down:{asset.asset_id}"]]), 6)] for asset in portfolio.assets_of("load_class")}
    shift_up = {asset.asset_id: [round(max(0.0, solution[indexes[f"shift_up:{asset.asset_id}"]]), 6)] for asset in portfolio.assets_of("load_class")}
    rebound = {asset.asset_id: [round(max(0.0, solution[indexes[f"rebound:{asset.asset_id}"]]), 6)] for asset in portfolio.assets_of("load_class")}
    reduce_ = {asset.asset_id: [round(max(0.0, solution[indexes[f"reduce:{asset.asset_id}"]]), 6)] for asset in portfolio.assets_of("load_class")}
    provenance = _prov(replay_id, path.path_sha256 or path.scenario_id, solver.method_hash)
    return ActionProposal(
        proposal_id=f"{replay_id}:proposal:{slot_index}",
        replay_id=replay_id,
        decision_id=f"{replay_id}:decision:{slot_index}",
        commit_slot=slot_index,
        renewable_dispatch_mw=renewable,
        bess_charge_mw=charge,
        bess_discharge_mw=discharge,
        grid_import_mw=[round(max(0.0, solution[indexes["grid_import"]]), 6)],
        grid_export_mw=[round(max(0.0, solution[indexes["grid_export"]]), 6)],
        shed_mw=shed,
        shift_down_mw=shift_down,
        shift_up_mw=shift_up,
        rebound_mw=rebound,
        reduce_mw=reduce_,
        market_intents=[intent for intent in portfolio.market_intents if market_delivery_due(intent, delivery_slot=slot_index)],
        policy_mode=policy_mode,
        solver_result=solver,
        provenance=provenance,
        expires_at=portfolio.fixture_start_utc + dt.timedelta(minutes=(slot_index + 1) * 15),
    )


def plan_next(
    portfolio: PortfolioFixture,
    path: ScenarioPath,
    events: Iterable[EventRecord],
    *,
    replay_id: str,
    slot_index: int,
    policy_mode: PolicyMode,
    energy_state: dict[str, float] | None = None,
    previous_flow_mw: float = 0.0,
    deadline: float | None = None,
    request_budget_seconds: float = DEFAULT_PER_REQUEST_BUDGET_SECONDS,
    slot_budget_seconds: float = DEFAULT_PER_SLOT_BUDGET_SECONDS,
    force_solver_unavailable: bool = False,
    forced_fallback_reason: str | None = None,
    scenario_paths: Iterable[ScenarioPath] | None = None,
    dr_allowed_load_ids: set[str] | None = None,
    carbon_factor: CarbonFactorAssumption | None = None,
    objective_order: tuple[str, ...] | None = None,
) -> PlanResult:
    events = list(events)
    now = monotonic()
    # The agentic layer may supply a bespoke lexicographic ordering (P4 F2). It is
    # validated by the agent to known objectives with a safety tier first; when
    # absent we use the fixed policy preset exactly as before.
    active_objectives = tuple(objective_order) if objective_order else POLICY_OBJECTIVES[policy_mode]
    effective_deadline = deadline if deadline is not None else now + slot_budget_seconds
    remaining = max(0.0, min(effective_deadline - now, request_budget_seconds, slot_budget_seconds))
    carbon_factor = carbon_factor or load_carbon_factor()
    if scenario_paths is None:
        from .scenarios import generate_scenarios
        paths = list(generate_scenarios(portfolio, seed=path.seed).paths)
    else:
        paths = list(scenario_paths)
    if path.scenario_id not in {candidate.scenario_id for candidate in paths}:
        paths.insert(0, path)
    reason: str | None = None
    result = None
    status = "UNAVAILABLE"
    message = "scenario planner not attempted"
    if force_solver_unavailable:
        reason = forced_fallback_reason or "SOLVER_UNAVAILABLE"
        status = "UNAVAILABLE"
        message = "solver explicitly unavailable; deterministic fallback selected"
    elif remaining <= 0.0:
        reason = "FALLBACK_BUDGET_EXHAUSTED"
        status = "TIME_LIMIT"
        message = "solver budget exhausted before scenario model"
    else:
        model, _ = _build_model(
            portfolio,
            paths,
            events,
            slot_index=slot_index,
            energy_state=energy_state,
            previous_flow_mw=previous_flow_mw,
            dr_allowed_load_ids=dr_allowed_load_ids,
            carbon_factor=carbon_factor,
        )
        result, status, message = _solve_lexicographic(
            model,
            objective_order=active_objectives,
            deadline=effective_deadline,
            request_budget_seconds=request_budget_seconds,
            slot_budget_seconds=slot_budget_seconds,
        )
        if status != "OPTIMAL":
            reason = "SOLVER_TIMEOUT" if status == "TIME_LIMIT" else ("SOLVER_UNAVAILABLE" if status == "UNAVAILABLE" else ("SOLVER_INFEASIBLE" if status == "INFEASIBLE" else "SOLVER_ERROR"))

    if status == "OPTIMAL" and result is not None:
        solution, stage_values, solver_version = result
        model, _ = _build_model(
            portfolio,
            paths,
            events,
            slot_index=slot_index,
            energy_state=energy_state,
            previous_flow_mw=previous_flow_mw,
            dr_allowed_load_ids=dr_allowed_load_ids,
            carbon_factor=carbon_factor,
        )
        objectives = _objective_values(model, solution, portfolio, paths, events, slot_index=slot_index, energy_state=energy_state, carbon_factor=carbon_factor)
        stage_lookup = {name: objectives.get(name, value) for name, value in stage_values}
        stages = [StageOptimum(stage_name=f"tier-{index}", objective_name=name, value=round(stage_lookup.get(name, 0.0), 6), unit="MWh" if "margin" in name or "reliability" in name else None, fixed_tolerance=BESS_NUMERIC_TOLERANCE_MWH, provenance=_prov(replay_id, path.scenario_id)) for index, name in enumerate(active_objectives, start=1)]
        solver = SolverResult(
            status="OPTIMAL",
            solver_name="scipy.optimize.milp/HiGHS",
            solver_version=solver_version,
            budget_seconds=round(min(request_budget_seconds, slot_budget_seconds), 6),
            objective_values=[round(stage_lookup.get(name, 0.0), 6) for name in active_objectives],
            stage_optima=stages,
            message=message,
            fallback_reason=None,
            method_hash=PLANNER_METHOD_HASH,
            provenance=_prov(replay_id, path.scenario_id),
        )
        candidate = _proposal_from_solution(portfolio, path, paths, model, solution, replay_id=replay_id, slot_index=slot_index, policy_mode=policy_mode, solver=solver)
        used_fallback = False
    else:
        fallback_reason = reason or "SOLVER_ERROR"
        candidate, fallback_solver, _ = fallback_plan(
            portfolio,
            path,
            events,
            replay_id=replay_id,
            slot_index=slot_index,
            policy_mode=policy_mode,
            energy_state=energy_state,
            previous_flow_mw=previous_flow_mw,
            reason=fallback_reason,
            deadline=effective_deadline,
            dr_allowed_load_ids=dr_allowed_load_ids,
        )
        solver = fallback_solver.model_copy(update={"message": message, "fallback_reason": fallback_reason})
        candidate = candidate.model_copy(update={"solver_result": solver})
        used_fallback = True
        stages = []

    tool = ToolCall(
        tool_name="validate_proposal",
        request_id=f"{replay_id}:tool:{slot_index}",
        arguments_digest=sha256_hex({"proposal_id": candidate.proposal_id, "slot": slot_index, "scenario_ids": [item.scenario_id for item in paths]}),
        result_digest=None,
        status="requested",
        provenance=_prov(replay_id, candidate.proposal_id),
    )
    trace = PlanningTrace(
        trace_id=f"{replay_id}:trace:{slot_index}",
        replay_id=replay_id,
        slot_index=slot_index,
        tool_calls=[tool],
        objective_sequence=list(active_objectives),
        stage_optima=candidate.solver_result.stage_optima,
        proposal_id=candidate.proposal_id,
        status="fallback" if used_fallback else "completed",
        provenance=_prov(replay_id, candidate.proposal_id),
    )
    return PlanResult(
        replay_id=replay_id,
        slot_index=slot_index,
        policy_mode=policy_mode,
        proposal=candidate,
        solver_result=candidate.solver_result,
        fallback_used=used_fallback,
        fallback_reason=candidate.solver_result.fallback_reason,
        trace=trace,
        provenance=_prov(replay_id, candidate.proposal_id),
    )
