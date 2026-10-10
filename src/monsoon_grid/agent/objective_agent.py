"""Autonomous objective-selection agent (P4 F2) with a reflective re-plan loop.

Pipeline per slot:

  situation features (incl. conformal uncertainty width)
    -> brain proposes a lexicographic objective ordering + rationale  [AUTONOMOUS]
    -> optimizer runs that ordering as a TOOL
    -> deterministic validator gates it as a TOOL  [SOLE SAFETY AUTHORITY]
    -> agent reflects: if rejected/poor, revise criteria and re-plan (bounded)
    -> regret vs the fixed preset is computed for honesty

Scientific framing:
  * Uncertainty-conditioned objective selection. The criteria choice is
    conditioned on the forecast interval width (P90-P10, a conformal-style band
    from forecasts.py). Wide bands push the agent toward reliability/reserve;
    tight bands permit cost-aggressive orderings. This links calibrated
    uncertainty to the decision policy rather than using a fixed objective.
  * Reflective self-critique. The agent reads its own validator outcome and
    re-derives criteria, a bounded ReAct-style loop with a hard iteration cap.
  * Regret logging. Each autonomous choice is compared to the declared preset so
    the benefit (or lack of it) is quantified honestly, never asserted.

Safety invariants (non-negotiable):
  * The agent only chooses the objective ORDERING. It never writes a setpoint,
    submits a market order, approves, or overrides a validator rejection.
  * Every objective name it emits must be a known planner objective, and a
    safety-tier objective is always forced first.
  * The brain (Gemini or deterministic) is advisory; the real optimizer and the
    real validator produce every number the agent reports.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..canonical import sha256_hex
from ..config import POLICY_OBJECTIVES
from ..contracts import EventRecord, PolicyMode, PortfolioFixture, ScenarioPath

# The complete set of objective names the planner can order (validated).
KNOWN_OBJECTIVES: frozenset[str] = frozenset({
    "critical_service_margin", "required_reliability_tier", "same_non_negotiable_safety_tier",
    "reserve_margin", "reliability_event_count", "net_cost", "carbon", "curtailment",
    "bess_wear", "dr_activation",
})
# At least one of these must lead every ordering (safety tier is never demoted).
SAFETY_TIER: frozenset[str] = frozenset({
    "critical_service_margin", "required_reliability_tier", "same_non_negotiable_safety_tier",
})
MAX_REFLECTION_STEPS = 3


@dataclass(frozen=True)
class SituationFeatures:
    """Structured, numeric situation summary handed to the brain (no free text)."""

    slot_index: int
    timestamp_utc: str
    renewable_available_mw: float
    demand_mw: float
    net_margin_mw: float            # renewable - demand
    price_usd_per_mwh: float
    forecast_band_width_mw: float   # P90 - P10 at this slot (conformal-style)
    uncertainty_ratio: float        # band width / max(renewable, 1)
    bess_soc_fraction: float
    bess_outage: bool
    tie_derated: bool
    weather_event: bool
    demand_spike: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "slot_index": self.slot_index, "timestamp_utc": self.timestamp_utc,
            "renewable_available_mw": round(self.renewable_available_mw, 3),
            "demand_mw": round(self.demand_mw, 3), "net_margin_mw": round(self.net_margin_mw, 3),
            "price_usd_per_mwh": round(self.price_usd_per_mwh, 3),
            "forecast_band_width_mw": round(self.forecast_band_width_mw, 3),
            "uncertainty_ratio": round(self.uncertainty_ratio, 4),
            "bess_soc_fraction": round(self.bess_soc_fraction, 4),
            "bess_outage": self.bess_outage, "tie_derated": self.tie_derated,
            "weather_event": self.weather_event, "demand_spike": self.demand_spike,
        }


@dataclass(frozen=True)
class AgentStep:
    """One reflective iteration: a proposed ordering and the resulting outcome."""

    iteration: int
    objective_order: list[str]
    rationale: str
    confidence: float
    validator_status: str
    critical_unserved_mwh: float
    net_cost_usd: float
    accepted: bool
    revised_reason: str | None = None


@dataclass(frozen=True)
class AgentAction:
    """One concrete action the agent's chosen criteria produced (P4 action vocab)."""

    category: str      # battery | market | renewable | demand
    action: str        # e.g. "Discharge BESS", "Sell to grid", "Curtail solar", "Trigger demand response"
    target: str        # asset id or "portfolio"
    magnitude_mw: float
    unit: str
    driver_objective: str  # which objective in the ordering drove this action
    note: str


@dataclass(frozen=True)
class AgentDecision:
    """The agent's autonomous decision for one slot, with its full trace."""

    replay_id: str
    slot_index: int
    brain: str                       # "gemini" | "deterministic"
    features: SituationFeatures
    chosen_objective_order: list[str]
    rationale: str
    confidence: float
    steps: list[AgentStep]
    actions: list[AgentAction]
    conflict_note: str
    preset_policy: PolicyMode
    preset_net_cost_usd: float
    preset_critical_unserved_mwh: float
    agent_net_cost_usd: float
    agent_critical_unserved_mwh: float
    cost_regret_usd: float           # agent - preset (negative = agent better)
    reliability_regret_mwh: float    # agent - preset (negative = agent better)
    forbidden_tools_blocked: list[str]
    digest: str = field(default="")

    def compute_digest(self) -> str:
        return sha256_hex({
            "replay": self.replay_id, "slot": self.slot_index, "brain": self.brain,
            "order": self.chosen_objective_order, "steps": len(self.steps),
            "agent_cost": round(self.agent_net_cost_usd, 6),
        })


def sanitize_order(order: list[str]) -> list[str]:
    """Keep only known objectives, force a safety tier first, de-duplicate."""
    seen: set[str] = set()
    cleaned = [name for name in order if name in KNOWN_OBJECTIVES and not (name in seen or seen.add(name))]
    if not cleaned or cleaned[0] not in SAFETY_TIER:
        lead = next((name for name in cleaned if name in SAFETY_TIER), "required_reliability_tier")
        cleaned = [lead] + [name for name in cleaned if name != lead]
    # Ensure every known objective is present so the lexicographic solve is total.
    for name in POLICY_OBJECTIVES["balanced"]:
        if name not in cleaned:
            cleaned.append(name)
    return cleaned


class AgentBrain(Protocol):
    name: str
    def propose(self, features: SituationFeatures, feedback: str | None) -> tuple[list[str], str, float]: ...


class DeterministicBrain:
    """Transparent, uncertainty-conditioned heuristic brain (always available).

    This is a real decision policy, not a stub: it reads the situation and the
    conformal band width and *derives* an objective ordering, shifting toward
    reliability under high uncertainty / stress and toward cost under calm, tight
    forecasts. It also responds to validator feedback in the reflective loop.
    """

    name = "deterministic"

    def propose(self, features: SituationFeatures, feedback: str | None) -> tuple[list[str], str, float]:
        f = features
        # Thresholds calibrated to the demo's real distributions: band ratio spans
        # ~0.31..0.64 (median 0.39); price spans ~41..96 (median 66). "Stress" is an
        # actual contingency or a severe deficit, not the ordinary nightly import.
        event_stress = f.bess_outage or f.tie_derated or f.demand_spike
        severe_deficit = f.net_margin_mw < -30.0
        high_uncertainty = f.uncertainty_ratio >= 0.46        # upper quartile of the band
        high_price = f.price_usd_per_mwh >= 85.0              # upper quartile of price
        surplus = f.net_margin_mw > 0
        escalate = feedback is not None

        if event_stress or severe_deficit or escalate:
            order = ["critical_service_margin", "reserve_margin", "reliability_event_count",
                     "net_cost", "carbon", "curtailment", "bess_wear", "dr_activation"]
            drivers = []
            if f.bess_outage:
                drivers.append("BESS outage")
            if f.tie_derated:
                drivers.append("tie-line derate")
            if f.demand_spike:
                drivers.append("demand spike")
            if severe_deficit:
                drivers.append(f"severe deficit {f.net_margin_mw:.0f} MW")
            why = ("Stress (" + ", ".join(drivers) + "); leading with critical-service and reserve "
                   "margins before cost." if drivers else
                   "Hardening reliability after validator feedback.")
            conf = 0.9 if (event_stress or severe_deficit) else 0.6
        elif high_uncertainty and not (high_price and surplus):
            order = ["required_reliability_tier", "reserve_margin", "net_cost", "carbon",
                     "curtailment", "bess_wear", "dr_activation"]
            why = (f"Forecast band is wide (uncertainty ratio {f.uncertainty_ratio:.2f}); "
                   "protecting reserve ahead of cost while uncertainty is elevated.")
            conf = 0.74
        elif high_price and surplus:
            order = ["same_non_negotiable_safety_tier", "net_cost", "curtailment", "carbon",
                     "bess_wear", "dr_activation"]
            why = (f"High price (${f.price_usd_per_mwh:.0f}/MWh) with {f.net_margin_mw:.0f} MW surplus; "
                   "pursuing cost/revenue aggressively above the fixed safety tier.")
            conf = 0.82
        else:
            order = list(POLICY_OBJECTIVES["balanced"])
            why = (f"Moderate conditions (uncertainty {f.uncertainty_ratio:.2f}, "
                   f"price ${f.price_usd_per_mwh:.0f}/MWh); a balanced ordering fits.")
            conf = 0.7
        if escalate:
            why += f" Revised after validator feedback: {feedback}"
        return sanitize_order(order), why, conf


class GeminiBrain:
    """Vertex AI Gemini brain. Falls back to the deterministic brain on any error.

    Emits a strict JSON object: {objective_order: [...], rationale: str,
    confidence: float}. The output is sanitized before use; the model never
    produces a number that reaches the operator — only an ordering + rationale.
    """

    name = "gemini"

    def __init__(self, model: str | None = None, project: str | None = None, location: str | None = None) -> None:
        self._model_name = model or os.environ.get("VAYU_GEMINI_MODEL", "gemini-2.5-flash")
        self._project = project or os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("VAYU_GCP_PROJECT")
        self._location = location or os.environ.get("VAYU_VERTEX_LOCATION", "us-central1")
        self._fallback = DeterministicBrain()
        self._model: Any = None
        self._init_error: str | None = None

    def _ensure_model(self) -> bool:
        if self._model is not None:
            return True
        try:
            from google import genai  # google-genai unified SDK, Vertex backend
            self._model = genai.Client(vertexai=True, project=self._project, location=self._location)
            return True
        except Exception as exc:  # pragma: no cover - exercised only with Vertex present
            self._init_error = f"{type(exc).__name__}: {exc}"
            return False

    def propose(self, features: SituationFeatures, feedback: str | None) -> tuple[list[str], str, float]:
        if not self._ensure_model():
            order, why, conf = self._fallback.propose(features, feedback)
            return order, f"[gemini unavailable: {self._init_error}] {why}", conf
        import json

        allowed = sorted(KNOWN_OBJECTIVES)
        prompt = (
            "You are the objective-selection agent for a renewable-energy portfolio "
            "orchestrator (solar, wind, batteries, grid, flexible loads). Decide the "
            "LEXICOGRAPHIC objective ordering the optimizer should use for THIS 15-minute "
            "slot, given the situation. A safety-tier objective (one of "
            "critical_service_margin, required_reliability_tier, same_non_negotiable_safety_tier) "
            "MUST be first. Prefer reliability/reserve when uncertainty or stress is high; "
            "prefer net_cost when the forecast band is tight and conditions are calm.\n"
            f"Allowed objective names (use only these): {allowed}\n"
            f"Situation: {json.dumps(features.as_dict())}\n"
            + (f"Prior attempt was rejected by the safety validator: {feedback}. Revise.\n" if feedback else "")
            + 'Respond with ONLY a JSON object: {"objective_order": [..], "rationale": "..", "confidence": 0..1}.'
        )
        try:  # pragma: no cover - exercised only with Vertex present
            response = self._model.models.generate_content(
                model=self._model_name,
                contents=prompt,
                config={"temperature": 0.2, "response_mime_type": "application/json"},
            )
            data = json.loads(response.text)
            order = sanitize_order([str(x) for x in data.get("objective_order", [])])
            rationale = str(data.get("rationale", "")).strip() or "Gemini objective selection."
            confidence = float(data.get("confidence", 0.7))
            return order, rationale, max(0.0, min(1.0, confidence))
        except Exception as exc:  # pragma: no cover
            order, why, conf = self._fallback.propose(features, feedback)
            detail = str(exc).replace("\n", " ")[:180]
            return order, f"[gemini error: {type(exc).__name__}: {detail}] {why}", conf


def build_brain(prefer: str | None = None) -> AgentBrain:
    """Pick the brain. 'gemini' only if explicitly enabled; else deterministic."""
    choice = (prefer or os.environ.get("VAYU_AGENT_BRAIN", "deterministic")).strip().lower()
    if choice == "gemini":
        return GeminiBrain()
    return DeterministicBrain()


@dataclass
class ObjectiveSelectionAgent:
    """Runs the reflective objective-selection loop for a slot using the planner
    and validator as tools. Pure orchestration; holds no safety authority."""

    portfolio: PortfolioFixture
    brain: AgentBrain

    def _features(self, path: ScenarioPath, events: list[EventRecord], generation_mw: dict[str, list[float]],
                  demand_mw: dict[str, list[float]], forecasts: list[Any], soc: float, slot_index: int,
                  timestamp_utc: str) -> SituationFeatures:
        renewable = sum(series[slot_index] for series in generation_mw.values())
        demand = sum(series[slot_index] for series in demand_mw.values())
        # Conformal-style band width: sum of renewable-forecast P90-P10 at this slot.
        band = 0.0
        for fb in forecasts:
            variable = getattr(fb, "variable", None)
            if variable in {"solar", "wind"}:
                p10 = getattr(fb, "p10", [])
                p90 = getattr(fb, "p90", [])
                if slot_index < len(p10) and slot_index < len(p90):
                    band += max(0.0, p90[slot_index] - p10[slot_index])
        price = path.price_usd_per_mwh[slot_index]
        return SituationFeatures(
            slot_index=slot_index, timestamp_utc=timestamp_utc,
            renewable_available_mw=renewable, demand_mw=demand, net_margin_mw=renewable - demand,
            price_usd_per_mwh=price, forecast_band_width_mw=band,
            uncertainty_ratio=band / max(renewable, 1.0), bess_soc_fraction=soc,
            bess_outage=any(e.event_type == "bess_outage" and e.start_slot <= slot_index <= e.end_slot for e in events),
            tie_derated=any(e.event_type == "tie_derate" and e.start_slot <= slot_index <= e.end_slot for e in events),
            weather_event=any(e.event_type == "monsoon_cloud" and e.start_slot <= slot_index <= e.end_slot for e in events),
            demand_spike=any(e.event_type == "demand_spike" and e.start_slot <= slot_index <= e.end_slot for e in events),
        )

    def deliberate(
        self,
        path: ScenarioPath,
        events: list[EventRecord],
        *,
        replay_id: str,
        slot_index: int,
        generation_mw: dict[str, list[float]],
        demand_mw: dict[str, list[float]],
        forecasts: list[Any],
        energy_state: dict[str, float] | None,
        preset_policy: PolicyMode = "balanced",
        timestamp_utc: str = "",
        max_steps: int = MAX_REFLECTION_STEPS,
    ) -> AgentDecision:
        """Run the reflective objective-selection loop and compute regret vs preset."""
        from ..dispatch import default_energy
        from ..planner import plan_next
        from ..validator import validate_proposal

        energy = energy_state or default_energy(self.portfolio)
        bess_specs = [a for a in self.portfolio.assets if getattr(a, "kind", None) == "bess"]
        soc = 0.0
        if bess_specs:
            total_cap = sum(getattr(a, "capacity_mwh", 1.0) for a in bess_specs)
            soc = sum(energy.get(a.asset_id, 0.0) for a in bess_specs) / max(total_cap, 1.0)
        features = self._features(path, events, generation_mw, demand_mw, forecasts, soc, slot_index, timestamp_utc)

        def run(order: tuple[str, ...]) -> tuple[Any, Any]:
            plan = plan_next(self.portfolio, path, events, replay_id=replay_id, slot_index=slot_index,
                             policy_mode=preset_policy, energy_state=energy, objective_order=order)
            decision = validate_proposal(self.portfolio, path, events, plan.proposal,
                                         replay_id=replay_id, slot_index=slot_index, energy_state=energy)
            return plan, decision

        steps: list[AgentStep] = []
        feedback: str | None = None
        chosen_order: list[str] = []
        chosen_rationale = ""
        chosen_conf = 0.0
        agent_plan = agent_decision = None
        for iteration in range(1, max_steps + 1):
            order, rationale, confidence = self.brain.propose(features, feedback)
            plan, decision = run(tuple(order))
            crit = float(decision.critical_unserved_mwh)
            cost = float(getattr(plan.proposal, "expected_net_cost_usd", 0.0) or _net_cost_of(plan))
            accepted = decision.status == "ACCEPTED"
            steps.append(AgentStep(
                iteration=iteration, objective_order=order, rationale=rationale, confidence=confidence,
                validator_status=decision.status, critical_unserved_mwh=round(crit, 6),
                net_cost_usd=round(cost, 6), accepted=accepted, revised_reason=feedback,
            ))
            chosen_order, chosen_rationale, chosen_conf = order, rationale, confidence
            agent_plan, agent_decision = plan, decision
            if accepted and crit <= 1e-6:
                break
            # Reflect: tell the brain what went wrong so it hardens next iteration.
            violation_codes = ",".join(v.code for v in decision.violations[:4]) or "poor_reliability"
            feedback = (f"status={decision.status}, critical_unserved={crit:.3f} MWh, "
                        f"violations=[{violation_codes}]")

        # Concrete action cluster the chosen criteria produced (P4 "takes actions").
        actions, conflict_note = extract_actions(agent_plan.proposal, chosen_order, features)

        # Regret vs the declared preset (same situation, fixed ordering).
        preset_plan, preset_decision = run(POLICY_OBJECTIVES[preset_policy])
        agent_cost = float(getattr(agent_plan.proposal, "expected_net_cost_usd", 0.0) or _net_cost_of(agent_plan))
        preset_cost = float(getattr(preset_plan.proposal, "expected_net_cost_usd", 0.0) or _net_cost_of(preset_plan))
        agent_crit = float(agent_decision.critical_unserved_mwh)
        preset_crit = float(preset_decision.critical_unserved_mwh)

        result = AgentDecision(
            replay_id=replay_id, slot_index=slot_index, brain=self.brain.name, features=features,
            chosen_objective_order=chosen_order, rationale=chosen_rationale, confidence=chosen_conf,
            steps=steps, actions=actions, conflict_note=conflict_note, preset_policy=preset_policy,
            preset_net_cost_usd=round(preset_cost, 6), preset_critical_unserved_mwh=round(preset_crit, 6),
            agent_net_cost_usd=round(agent_cost, 6), agent_critical_unserved_mwh=round(agent_crit, 6),
            cost_regret_usd=round(agent_cost - preset_cost, 6),
            reliability_regret_mwh=round(agent_crit - preset_crit, 6),
            forbidden_tools_blocked=sorted({"write_setpoint", "submit_market_order", "approve_self", "scada_command", "relay_command"}),
        )
        object.__setattr__(result, "digest", result.compute_digest())
        return result


def _net_cost_of(plan: Any) -> float:
    """Best-effort net-cost read from a plan result's solver objective values."""
    try:
        order = plan.trace.objective_sequence
        values = plan.solver_result.objective_values
        if "net_cost" in order:
            return float(values[order.index("net_cost")])
    except Exception:
        pass
    return 0.0


_RESERVE_LABEL = {"critical_service_margin": "critical service", "required_reliability_tier": "reliability tier",
                  "same_non_negotiable_safety_tier": "safety floor", "reserve_margin": "reserve margin",
                  "net_cost": "net cost", "curtailment": "curtailment", "carbon": "carbon",
                  "bess_wear": "battery wear", "dr_activation": "demand response", "reliability_event_count": "reliability"}


def extract_actions(proposal: Any, order: list[str], features: SituationFeatures) -> tuple[list[AgentAction], str]:
    """Map the realized ActionProposal into the brief's action vocabulary, each
    attributed to the objective in the chosen ordering that most plausibly drove
    it, plus a human conflict note describing how ties were broken.

    This reads the OPTIMIZER's realized dispatch (not the agent's words); it is
    the concrete 'the agent takes actions' surface the problem statement asks for.
    """
    eps = 0.05
    actions: list[AgentAction] = []
    lead = order[0] if order else "required_reliability_tier"
    cost_rank = order.index("net_cost") if "net_cost" in order else 99
    reliability_leads = lead in SAFETY_TIER

    def first(mapping: dict[str, list[float]]) -> dict[str, float]:
        return {k: (v[0] if isinstance(v, list) and v else 0.0) for k, v in (mapping or {}).items()}

    charge = first(getattr(proposal, "bess_charge_mw", {}))
    discharge = first(getattr(proposal, "bess_discharge_mw", {}))
    shed = first(getattr(proposal, "shed_mw", {}))
    shift_down = first(getattr(proposal, "shift_down_mw", {}))
    renewable = first(getattr(proposal, "renewable_dispatch_mw", {}))
    import_mw = (getattr(proposal, "grid_import_mw", [0.0]) or [0.0])[0]
    export_mw = (getattr(proposal, "grid_export_mw", [0.0]) or [0.0])[0]

    # Battery actions.
    for bess_id, mw in sorted(charge.items()):
        if mw > eps:
            actions.append(AgentAction("battery", "Charge battery", bess_id, round(mw, 2), "MW",
                                       "net_cost" if cost_rank < 2 else "reserve_margin",
                                       "Storing surplus/cheap energy for later reliability or arbitrage."))
    for bess_id, mw in sorted(discharge.items()):
        if mw > eps:
            actions.append(AgentAction("battery", "Discharge battery", bess_id, round(mw, 2), "MW",
                                       lead if reliability_leads else "net_cost",
                                       "Covering demand from storage to protect supply / avoid costly import."))
    if not any(a.category == "battery" for a in actions):
        actions.append(AgentAction("battery", "Hold / reserve capacity", "portfolio", 0.0, "MW",
                                    "reserve_margin", "Batteries held; reserve protected for contingencies."))

    # Market actions.
    if import_mw > eps:
        actions.append(AgentAction("market", "Buy from grid", "grid-main", round(import_mw, 2), "MW",
                                    lead if reliability_leads else "net_cost",
                                    "Importing to meet demand the portfolio cannot self-supply this slot."))
    if export_mw > eps:
        actions.append(AgentAction("market", "Sell to grid", "grid-main", round(export_mw, 2), "MW",
                                    "net_cost", "Exporting surplus; valued against the slot price."))
    if import_mw <= eps and export_mw <= eps:
        hint = "Holding — delaying sale while price/− uncertainty is unfavourable." if features.price_usd_per_mwh < 85 else "Holding — no beneficial trade this slot."
        actions.append(AgentAction("market", "Hold / delay trade", "grid-main", 0.0, "MW", "net_cost", hint))

    # Renewable actions.
    curtail_total = 0.0
    cap_hint = features.renewable_available_mw
    dispatched = sum(renewable.values())
    curtail_total = max(0.0, cap_hint - dispatched) if cap_hint > dispatched + eps else 0.0
    if curtail_total > eps:
        actions.append(AgentAction("renewable", "Curtail renewables", "portfolio", round(curtail_total, 2), "MW",
                                    "curtailment", "Trimming generation that cannot be used, stored, or exported."))
    else:
        actions.append(AgentAction("renewable", "Dispatch all available renewables", "portfolio", round(dispatched, 2), "MW",
                                    "curtailment", "No curtailment — maximizing clean-energy utilization."))

    # Demand management.
    dr_total = sum(shed.values()) + sum(shift_down.values())
    if dr_total > eps:
        actions.append(AgentAction("demand", "Trigger demand response", "flexible loads", round(dr_total, 2), "MW",
                                    "dr_activation", "Shedding/shifting priced-DR load to balance the slot."))
    else:
        actions.append(AgentAction("demand", "No demand response", "flexible loads", 0.0, "MW",
                                    "dr_activation", "Flexible loads served normally; critical load never shed."))

    # Conflict note: how the ordering broke the key trade-off this slot.
    if reliability_leads and cost_rank >= 2:
        conflict = (f"Objectives conflicted: reliability vs cost. The agent ranked "
                    f"'{_RESERVE_LABEL.get(lead, lead)}' above net cost, so it protected supply "
                    f"(discharge/import/reserve) even where that was not the cheapest action.")
    elif cost_rank <= 1:
        conflict = ("Objectives conflicted: cost vs curtailment/wear. With the forecast tight and "
                    "conditions calm, the agent ranked net cost high, favouring cheaper dispatch and "
                    "export while keeping the mandatory safety floor first.")
    else:
        conflict = ("Objectives were broadly aligned this slot; the chosen ordering did not force a "
                    "material trade-off between reliability and cost.")
    return actions, conflict
