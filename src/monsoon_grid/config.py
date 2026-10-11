"""Configuration constants and product paths."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PRODUCT_ROOT / "src"
_runtime_override = os.environ.get("MONSOON_GRID_RUNTIME_ROOT")
RUNTIME_ROOT = Path(_runtime_override).resolve() if _runtime_override else PRODUCT_ROOT / "runtime"
SITE_PACKAGES = RUNTIME_ROOT / "site-packages"
STORE_ROOT = RUNTIME_ROOT / "store"
ARTIFACT_ROOT = RUNTIME_ROOT / "artifacts"
PROVENANCE_ROOT = RUNTIME_ROOT / "provenance"
KEY_ROOT = RUNTIME_ROOT / "keys"
LOG_ROOT = RUNTIME_ROOT / "logs"
TRUST_ROOT = PRODUCT_ROOT / "trust"
FIXTURE_ROOT = PRODUCT_ROOT / "fixtures"
SCHEMA_VERSION = "1.0"
PRODUCT_VERSION = "1.0.0"
REPLAY_VERSION = "monsoon-grid-replay-1"
SLOT_COUNT = 96
DT_HOURS = 0.25
SLOT_MINUTES = 15
BESS_NUMERIC_TOLERANCE_MWH = 0.000001
PSD_TOLERANCE = -1e-10
REPLAY_EXTERNAL_BUDGET_SECONDS = 300.0
REPLAY_BUDGET_SECONDS = 285.0
FINALIZATION_BUDGET_SECONDS = 15.0
DEFAULT_PER_SLOT_BUDGET_SECONDS = 2.0
DEFAULT_PER_REQUEST_BUDGET_SECONDS = 8.0
FALLBACK_ALGORITHM_VERSION = "fallback-rule-table-v1"
SCENARIO_METHOD_VERSION = "gaussian-copula-pcg64-v1"
# F2 maintenance: a dispatched inspection crew shortens an outage/maintenance
# window by this many 15-minute slots (30 minutes). A crew never extends a
# window and never shortens it below the current slot.
CREW_SPEEDUP_SLOTS = 2
MARKET_SUBMISSION_LAG_SLOTS = 4
MARKET_AUCTION_SESSION_SLOTS = 2

# Single source of truth for "which fixtures define a run" (Q7).
FIXTURE_SET_FILES: tuple[str, ...] = (
    "carbon_factor.json",
    "demo_events.json",
    "demo_obligations.json",
    "demo_portfolio.json",
    "demo_profiles.json",
    "demo_weather_context.json",
    "dsm_regimes.json",
    "flexibility_costs.json",
    "market_settlement.json",
    "obligation_heuristic.json",
)

POLICY_OBJECTIVES: dict[str, tuple[str, ...]] = {
    "reliability_first": (
        "critical_service_margin",
        "reserve_margin",
        "reliability_event_count",
        "net_cost",
        "carbon",
        "curtailment",
        "bess_wear",
    ),
    "balanced": (
        "required_reliability_tier",
        "net_cost",
        "carbon",
        "curtailment",
        "bess_wear",
        "dr_activation",
    ),
    "cost_first": (
        "same_non_negotiable_safety_tier",
        "net_cost",
        "curtailment",
        "carbon",
        "bess_wear",
        "dr_activation",
    ),
}

@dataclass(frozen=True)
class Settings:
    numeric_mode: str = "auto"
    replay_budget_seconds: float = REPLAY_BUDGET_SECONDS
    finalization_budget_seconds: float = FINALIZATION_BUDGET_SECONDS
    per_slot_budget_seconds: float = DEFAULT_PER_SLOT_BUDGET_SECONDS
    per_request_budget_seconds: float = DEFAULT_PER_REQUEST_BUDGET_SECONDS
    trust_keyring: Path | None = None
    trust_fingerprint: str | None = None
    trust_valid_at: str = "2026-07-01T00:00:00Z"

    @classmethod
    def from_env(cls) -> "Settings":
        keyring = os.environ.get("MONSOON_GRID_TRUST_KEYRING")
        return cls(
            numeric_mode=os.environ.get("MONSOON_GRID_NUMERIC_MODE", "auto"),
            replay_budget_seconds=float(os.environ.get("MONSOON_GRID_REPLAY_BUDGET_SECONDS", REPLAY_BUDGET_SECONDS)),
            finalization_budget_seconds=float(os.environ.get("MONSOON_GRID_FINALIZATION_BUDGET_SECONDS", FINALIZATION_BUDGET_SECONDS)),
            per_slot_budget_seconds=float(os.environ.get("MONSOON_GRID_PER_SLOT_BUDGET_SECONDS", DEFAULT_PER_SLOT_BUDGET_SECONDS)),
            per_request_budget_seconds=float(os.environ.get("MONSOON_GRID_PER_REQUEST_BUDGET_SECONDS", DEFAULT_PER_REQUEST_BUDGET_SECONDS)),
            trust_keyring=Path(keyring).resolve() if keyring else None,
            trust_fingerprint=os.environ.get("MONSOON_GRID_TRUST_FINGERPRINT"),
            trust_valid_at=os.environ.get("MONSOON_GRID_TRUST_VALID_AT", "2026-07-01T00:00:00Z"),
        )


def ensure_runtime_dirs() -> None:
    for path in (SITE_PACKAGES, STORE_ROOT, ARTIFACT_ROOT, PROVENANCE_ROOT, KEY_ROOT, LOG_ROOT, STORE_ROOT / "sha256"):
        path.mkdir(parents=True, exist_ok=True)
