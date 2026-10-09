"""Decision narration: human-readable description of planner and validator output.

This module renders already-computed solver and safety-validator output as prose.
It is NOT an autonomous multi-agent controller: it does not plan, does not
validate, does not constrain the optimizer, and holds no agency. The specialist
"thoughts" below are descriptive observations of fixture events and market
context; they are narration, not recommendations.

Replaces the former ``multi_agent_framework.py`` (design B5). Nothing here reads
the wall clock -- every timestamp is the replay clock from
``quality.replay_time``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

from .canonical import sha256_hex
from .contracts import (
    EventRecord,
    PlanResult,
    PolicyMode,
    PortfolioFixture,
    SafetyDecision,
    ScenarioPath,
)
from .quality import replay_time


class AgentRole(str, Enum):
    WEATHER_NOWCASTER = "weather_nowcaster"
    MARKET_ARBITRAGEUR = "market_arbitrageur"
    ASSET_HEALTH_CUSTODIAN = "asset_health_custodian"
    GRID_STABILITY_GOVERNOR = "grid_stability_governor"
    CHIEF_ORCHESTRATOR = "chief_orchestrator"


@dataclass(frozen=True)
class AgentThought:
    """A structured narration step describing observed context (no agency)."""

    role: AgentRole
    timestamp_utc: str
    observation: str
    assessment: str
    observed_context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionNarration:
    """Narration trace describing one slot's planner and validator output."""

    replay_id: str
    slot_index: int
    policy_mode: PolicyMode
    thoughts: list[AgentThought]
    consensus_rationale: str
    plan_result: PlanResult
    safety_decision: SafetyDecision
    digest: str = field(default="")

    def compute_digest(self) -> str:
        payload = {
            "replay": self.replay_id,
            "slot": self.slot_index,
            "policy": self.policy_mode,
            "thoughts": [t.__dict__ for t in self.thoughts],
            "plan_id": self.plan_result.record_id,
            "safety_id": self.safety_decision.record_id or self.safety_decision.decision_id,
        }
        return sha256_hex(payload)


class WeatherNowcastAgent:
    """Describes atmospheric context from fixture events (narration only)."""

    def evaluate(self, timestamp: str, slot_index: int, events: Iterable[EventRecord]) -> AgentThought:
        has_storm = any(e.event_type == "monsoon_cloud" or "storm" in str(e.details).lower() for e in events)
        has_wind_shear = any("wind" in str(e.details).lower() for e in events)
        if has_storm:
            obs = f"Slot {slot_index}: monsoon cloud front present in the fixture events."
            assess = "Solar availability is reduced for this slot per the scenario path."
            ctx = {"solar_derate": 0.50, "ramp_risk": "HIGH"}
        elif has_wind_shear:
            obs = f"Slot {slot_index}: a wind-related event is present in the fixture."
            assess = "Wind availability follows the scenario path for this slot."
            ctx = {"wind_derate": 1.0, "ramp_risk": "MODERATE"}
        else:
            obs = f"Slot {slot_index}: no weather event present in the fixture."
            assess = "Renewable availability follows the P50 scenario path."
            ctx = {"solar_derate": 1.0, "ramp_risk": "LOW"}
        return AgentThought(role=AgentRole.WEATHER_NOWCASTER, timestamp_utc=timestamp, observation=obs, assessment=assess, observed_context=ctx)


class MarketArbitrageAgent:
    """Describes market-price context (narration only)."""

    def evaluate(self, timestamp: str, slot_index: int, market_price_usd: float, events: Iterable[EventRecord]) -> AgentThought:
        is_spike = market_price_usd > 120.0 or any("price" in str(e.details).lower() for e in events)
        if is_spike:
            obs = f"Slot {slot_index}: scenario market price is ${market_price_usd:.2f}/MWh (high)."
            assess = "A high price makes committed export more valuable in net_cost terms."
            ctx = {"market_mode": "HIGH_PRICE", "avoid_import": True}
        elif market_price_usd < 35.0:
            obs = f"Slot {slot_index}: scenario market price is ${market_price_usd:.2f}/MWh (low)."
            assess = "A low price makes grid import comparatively cheap in net_cost terms."
            ctx = {"market_mode": "LOW_PRICE", "avoid_import": False}
        else:
            obs = f"Slot {slot_index}: scenario market price is ${market_price_usd:.2f}/MWh (nominal)."
            assess = "Price is in the nominal band for this slot."
            ctx = {"market_mode": "BALANCED", "avoid_import": False}
        return AgentThought(role=AgentRole.MARKET_ARBITRAGEUR, timestamp_utc=timestamp, observation=obs, assessment=assess, observed_context=ctx)


class AssetHealthCustodian:
    """Describes BESS state context (narration only)."""

    def evaluate(self, timestamp: str, slot_index: int, energy_state: dict[str, float] | None, events: Iterable[EventRecord]) -> AgentThought:
        bess_a_mwh = (energy_state or {}).get("bess-a", 14.0)
        bess_b_mwh = (energy_state or {}).get("bess-b", 11.0)
        bess_b_outage = any(e.event_type == "bess_outage" or ("bess-b" in e.affected_ids and "outage" in str(e.details).lower()) for e in events)
        if bess_b_outage:
            obs = f"Slot {slot_index}: BESS-B is in a fixture outage event; only BESS-A is modelled available."
            assess = "The planner model marks BESS-B unavailable for this slot."
            ctx = {"bess_b_available": False, "reserve_protection_mwh": 4.0}
        else:
            obs = f"Slot {slot_index}: BESS-A at {bess_a_mwh:.1f} MWh, BESS-B at {bess_b_mwh:.1f} MWh."
            assess = "Both batteries are modelled available for this slot."
            ctx = {"bess_b_available": True, "reserve_protection_mwh": 7.0}
        return AgentThought(role=AgentRole.ASSET_HEALTH_CUSTODIAN, timestamp_utc=timestamp, observation=obs, assessment=assess, observed_context=ctx)


class GridStabilityGovernor:
    """Describes tie-line context (narration only)."""

    def evaluate(self, timestamp: str, slot_index: int, events: Iterable[EventRecord]) -> AgentThought:
        tie_derated = any(e.event_type == "tie_derate" or "tie-main" in e.affected_ids for e in events)
        if tie_derated:
            obs = f"Slot {slot_index}: a tie_derate event is present in the fixture."
            assess = "The effective tie capacity is reduced for this slot per the event detail."
            ctx = {"tie_derated": True, "critical_load_priority": "MAXIMUM"}
        else:
            obs = f"Slot {slot_index}: no tie derate event present."
            assess = "The tie capacity follows the fixture nameplate and derate_fraction."
            ctx = {"tie_derated": False, "critical_load_priority": "STANDARD"}
        return AgentThought(role=AgentRole.GRID_STABILITY_GOVERNOR, timestamp_utc=timestamp, observation=obs, assessment=assess, observed_context=ctx)


DISCLAIMER = (
    "Narration only. These statements describe planner and validator output. They "
    "do not constrain the optimizer, are not produced by an autonomous agent, and "
    "are not a recommendation."
)


class DecisionNarrator:
    """Renders solver and validator output as prose. Holds no agency.

    It does not run the planner or the validator; it is given an already-computed
    ``PlanResult`` and ``SafetyDecision`` and only describes them.
    """

    def __init__(self, portfolio: PortfolioFixture) -> None:
        self.portfolio = portfolio
        self.weather_agent = WeatherNowcastAgent()
        self.market_agent = MarketArbitrageAgent()
        self.storage_agent = AssetHealthCustodian()
        self.grid_agent = GridStabilityGovernor()

    def narrate_decision(
        self,
        path: ScenarioPath,
        events: Iterable[EventRecord],
        *,
        replay_id: str,
        slot_index: int,
        policy_mode: PolicyMode,
        plan_result: PlanResult,
        safety_decision: SafetyDecision,
        energy_state: dict[str, float] | None = None,
        previous_flow_mw: float = 0.0,
    ) -> DecisionNarration:
        event_list = list(events)
        timestamp = replay_time(self.portfolio.fixture_start_utc, slot_index).isoformat()
        price_val = 70.0
        if "price" in self.portfolio.profiles_mw and slot_index < len(self.portfolio.profiles_mw["price"]):
            price_val = self.portfolio.profiles_mw["price"][slot_index]

        t_weather = self.weather_agent.evaluate(timestamp, slot_index, event_list)
        t_market = self.market_agent.evaluate(timestamp, slot_index, price_val, event_list)
        t_storage = self.storage_agent.evaluate(timestamp, slot_index, energy_state, event_list)
        t_grid = self.grid_agent.evaluate(timestamp, slot_index, event_list)
        thoughts = [t_weather, t_market, t_storage, t_grid]

        consensus_text = (
            f"Narration for slot {slot_index} under policy '{policy_mode}': "
            f"the planner returned solver status {plan_result.proposal.solver_result.status} and the "
            f"safety validator returned {safety_decision.status}. "
            f"Weather context: {t_weather.observed_context.get('ramp_risk', 'LOW')}. "
            f"Market context: {t_market.observed_context.get('market_mode', 'BALANCED')}. "
            f"Storage context: BESS-B available={t_storage.observed_context.get('bess_b_available', True)}. "
            f"Grid context: tie_derated={t_grid.observed_context.get('tie_derated', False)}. "
            f"{DISCLAIMER}"
        )
        chief_thought = AgentThought(
            role=AgentRole.CHIEF_ORCHESTRATOR,
            timestamp_utc=timestamp,
            observation="Narration synthesized from four domain context observers.",
            assessment=consensus_text,
            observed_context={"policy_mode": policy_mode},
        )
        thoughts.append(chief_thought)

        narration = DecisionNarration(
            replay_id=replay_id,
            slot_index=slot_index,
            policy_mode=policy_mode,
            thoughts=thoughts,
            consensus_rationale=consensus_text,
            plan_result=plan_result,
            safety_decision=safety_decision,
        )
        object.__setattr__(narration, "digest", narration.compute_digest())
        return narration


__all__ = [
    "AgentRole",
    "AgentThought",
    "DecisionNarration",
    "DISCLAIMER",
    "WeatherNowcastAgent",
    "MarketArbitrageAgent",
    "AssetHealthCustodian",
    "GridStabilityGovernor",
    "DecisionNarrator",
]
