"""Shared value types, enums, and the strict Pydantic base (design §9.1, §12).

``StrictRecord`` mirrors the product's ``contracts.StrictModel`` discipline
(``extra="forbid"``, ``strict=True``) and recursively rejects non-finite floats.
It deliberately does NOT import ``contracts`` (invariant 14): library modules
under ``forecasting/`` import only ``canonical`` and ``errors`` from the product.

The six-decimal JSON-boundary rule lives in :mod:`interface` (§9.1), not here:
``StrictRecord`` is used for internal records (dataset manifests, run config)
where unrounded float64 must survive.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

# ---------------------------------------------------------------------------
# Enumerated value types (design §8.6, §8.3)
# ---------------------------------------------------------------------------

#: Unit literals used across the subsystem.
Unit = Literal[
    "MW",
    "MWh",
    "fraction",
    "USD_per_MWh",
    "W/m^2",
    "degC",
    "m/s",
    "hPa",
    "deg",
    "minutes",
    "count",
    "unitless",
]

#: Provenance labels. Any self-chosen number is ``scenario_assumption`` -- never
#: ``measured``.
ProvenanceLabel = Literal["measured", "derived", "scenario_assumption", "synthetic"]

#: What the dataset's power target represents (design §4.7, §8.3).
TargetSemantics = Literal["available", "delivered", "unknown"]

#: Which baseline the residual layer sits on (design §5.1, §8.3).
BaselineSource = Literal["physical_chain", "climatology", "provider_forecast"]

#: How confident we are about the as-issued lead (design §8.4.1).
AsIssuedConfidence = Literal["previous_runs_exact_lead", "dataset_native", "archive_best_match"]

#: Validation tier (design §8.2).
ValidationTier = Literal["A", "B", "C"]

#: Reporting-only curtailment flag (design §6.7). Never a feature, never a cell key.
CurtailmentStatus = Literal["curtailed", "not_curtailed", "not_identifiable"]

#: Per-slot band source (design §9.5, §15).
BandSource = Literal[
    "model",
    "physical_zero",
    "fallback_unconditional",
    "fallback_climatology",
]


# ---------------------------------------------------------------------------
# Strict base record
# ---------------------------------------------------------------------------


def _check_finite(value: Any) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("numeric values must be finite")
    elif isinstance(value, (list, tuple)):
        for item in value:
            _check_finite(item)
    elif isinstance(value, dict):
        for item in value.values():
            _check_finite(item)


class StrictRecord(BaseModel):
    """Closed, strict record base with a recursive finite-float validator.

    Unlike the product ``StrictModel``, this base does not enforce the
    six-decimal rule: internal float64 state (model weights, calibration state,
    dataset values) must survive unrounded. The six-decimal rule is applied only
    at the JSON boundary in :mod:`interface`.
    """

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_default=True,
        validate_assignment=True,
        frozen=False,
    )

    @model_validator(mode="after")
    def _finite_values(self) -> "StrictRecord":
        _check_finite(self.model_dump(mode="python"))
        return self


class Assumption(StrictRecord):
    """A single self-chosen parameter with its provenance (design §17)."""

    name: str
    value: float | int | str | bool
    unit: Unit
    source: str
    label: ProvenanceLabel
    sensitivity: str | None = None


__all__ = [
    "Unit",
    "ProvenanceLabel",
    "TargetSemantics",
    "BaselineSource",
    "AsIssuedConfidence",
    "ValidationTier",
    "CurtailmentStatus",
    "BandSource",
    "StrictRecord",
    "Assumption",
]
