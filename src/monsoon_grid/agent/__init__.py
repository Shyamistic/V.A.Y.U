"""VAYU agentic orchestration layer.

An autonomous objective-selection agent that satisfies P4 feature F2: it
*determines the optimality criteria* for the current situation (adjusting for
uncertainty) BEFORE the optimizer runs, instead of a human picking a fixed
policy mode. The deterministic validator remains the sole safety authority; the
agent shapes the objective ordering and never approves, overrides a rejection,
or issues a real-grid/market command.
"""

from .objective_agent import (
    AgentAction,
    AgentBrain,
    AgentDecision,
    AgentStep,
    DeterministicBrain,
    GeminiBrain,
    ObjectiveSelectionAgent,
    build_brain,
)

__all__ = [
    "AgentAction",
    "AgentBrain",
    "AgentDecision",
    "AgentStep",
    "DeterministicBrain",
    "GeminiBrain",
    "ObjectiveSelectionAgent",
    "build_brain",
]
