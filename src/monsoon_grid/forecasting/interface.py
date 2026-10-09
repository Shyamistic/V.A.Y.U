"""Part D -- typed integration boundary (design §9). NOT wired into the engine.

Models mirror (do NOT import) ``contracts.py`` style, including the product's
six-decimal rule, which applies at this JSON boundary only. Order of operations
is fixed: predict -> rearrange -> clip -> ROUND to 6 dp -> validate non-crossing
on the ROUNDED values (ties legal).

A coverage claim can only be made through :class:`CoverageAttestation`, whose
``validated`` field is DERIVED, never set by hand (§9.3). This is the structural
answer to the "calibrated-confidence unsupported" finding: a consumer cannot
render a confidence number without the evidence, the baseline, and the tier.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
BandSourceLiteral = Literal["model", "physical_zero", "fallback_unconditional", "fallback_climatology"]
BaselineSourceLiteral = Literal["physical_chain", "climatology", "provider_forecast"]
ValidationStatus = Literal["tier_a_external", "tier_b_pseudo_truth", "tier_c_synthetic", "none"]


def _round6(value: float) -> float:
    return round(float(value), 6)


def _finite6(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("numeric values must be finite")
    if abs(value) >= 1e-6 and round(value, 6) != value:
        raise ValueError("numeric values may have at most six decimal places")
    return value


class StrictRecord(BaseModel):
    """Mirror of the product's StrictModel discipline incl. the six-decimal rule."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, strict=True, validate_default=True)

    @model_validator(mode="after")
    def _finite_values(self):
        def check(value):
            if isinstance(value, float):
                _finite6(value)
            elif isinstance(value, list):
                for v in value:
                    check(v)
            elif isinstance(value, dict):
                for v in value.values():
                    check(v)

        check(self.model_dump(mode="python"))
        return self


def round_rearrange_validate(quantiles: list[list[float]]) -> list[list[float]]:
    """Round each quantile to 6 dp and validate non-crossing per slot on the
    ROUNDED values (ties legal). ``quantiles`` is (n_levels, n_slots)."""
    rounded = [[_round6(v) for v in row] for row in quantiles]
    n_levels = len(rounded)
    if n_levels == 0:
        return rounded
    n_slots = len(rounded[0])
    for s in range(n_slots):
        col = [rounded[i][s] for i in range(n_levels)]
        for a, b in zip(col, col[1:]):
            if b < a:  # ties (b == a) are legal
                raise ValueError(f"quantiles cross at slot {s} after rounding: {a} > {b}")
    return rounded


class CoverageAttestation(StrictRecord):
    """The only place a confidence claim may be made (§9.3)."""

    nominal_coverage: float
    quantile_levels: list[float]
    empirical_coverage_by_horizon: dict[str, float]
    mean_width_by_horizon: dict[str, float]
    empirical_coverage_by_regime: dict[str, float]
    mean_width_by_regime: dict[str, float]
    sample_count_by_cell: dict[str, int]
    regime_presence_counts: dict[str, int]
    calibration_cell_fallback: list[str]
    measured_on_dataset_id: str
    measured_on_dataset_sha256: str
    baseline_source: BaselineSourceLiteral
    validation_tier: Literal["A", "B", "C"]
    validation_status: ValidationStatus
    caveats: list[str]
    caveats_reviewed: bool = False
    validated: bool = False

    @model_validator(mode="after")
    def _derive_validated_and_check_caveats(self):
        # Coverage never without width: each horizon with coverage needs a width.
        for h in self.empirical_coverage_by_horizon:
            if h not in self.mean_width_by_horizon:
                raise ValueError(f"coverage for horizon {h!r} has no width")
        for r in self.empirical_coverage_by_regime:
            if r not in self.mean_width_by_regime:
                raise ValueError(f"coverage for regime {r!r} has no width")
        # caveats must be non-empty whenever validation_status != "none".
        if self.validation_status != "none" and len(self.caveats) == 0:
            raise ValueError("a validated attestation requires non-empty caveats")
        # validated is DERIVED, never asserted by hand.
        derived = (
            self.validation_status == "tier_a_external"
            and self.caveats_reviewed
            and bool(self.baseline_source)
        )
        object.__setattr__(self, "validated", derived)
        return self


class CalibratedBand(StrictRecord):
    band_id: str
    asset_id: str
    variable: str
    unit: Literal["MW"] = "MW"
    issue_time: dt.datetime
    valid_start: dt.datetime
    valid_end: dt.datetime
    slot_minutes: int
    quantile_levels: list[float]
    quantiles: list[list[float]]  # (n_levels, n_slots), rounded + non-crossing
    point_p50_mw: list[float]
    band_source: list[BandSourceLiteral]
    baseline_mw: list[float]
    baseline_source: BaselineSourceLiteral
    model_version: str
    calibration_version: str
    calibration_method: str
    model_artifact_sha256: str
    calibration_artifact_sha256: str
    coverage_attestation: CoverageAttestation

    @field_validator("asset_id", "band_id", "variable")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not ID_RE.fullmatch(v):
            raise ValueError("identifier must match [A-Za-z0-9_.:-]+")
        return v

    @field_validator("issue_time", "valid_start", "valid_end")
    @classmethod
    def _tz_aware(cls, v: dt.datetime) -> dt.datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware UTC")
        return v.astimezone(dt.timezone.utc)

    @model_validator(mode="after")
    def _validate_shapes_and_order(self):
        n_slots = len(self.band_source)
        if self.issue_time > self.valid_start:
            raise ValueError("issue_time must be <= valid_start")
        if self.valid_start > self.valid_end:
            raise ValueError("valid_start must be <= valid_end")
        if len(self.quantiles) != len(self.quantile_levels):
            raise ValueError("quantiles rows must match quantile_levels")
        for row in self.quantiles:
            if len(row) != n_slots:
                raise ValueError("each quantile row must have n_slots entries")
        if len(self.point_p50_mw) != n_slots:
            raise ValueError("point_p50_mw must have n_slots entries")
        if len(self.baseline_mw) != n_slots:
            raise ValueError("baseline_mw must have n_slots entries")
        # Non-crossing on the (already rounded) values; ties legal.
        for s in range(n_slots):
            col = [self.quantiles[i][s] for i in range(len(self.quantile_levels))]
            for a, b in zip(col, col[1:]):
                if b < a:
                    raise ValueError(f"quantiles cross at slot {s}")
        return self


class BandRequest(StrictRecord):
    issue_time: dt.datetime
    valid_start: dt.datetime
    valid_end: dt.datetime
    slot_minutes: int
    asset_id: str
    variable: str
    capacity_mw: float
    capacity_source: str
    quantile_levels: list[float]
    nominal_coverage: float
    model_artifact_id: str
    calibration_artifact_id: str
    offline: bool = False

    @field_validator("issue_time", "valid_start", "valid_end")
    @classmethod
    def _tz_aware(cls, v: dt.datetime) -> dt.datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware UTC")
        return v.astimezone(dt.timezone.utc)

    @field_validator("quantile_levels")
    @classmethod
    def _levels(cls, v: list[float]) -> list[float]:
        if not v or any(not (0.0 < x < 1.0) for x in v):
            raise ValueError("quantile_levels must be in (0,1)")
        if any(a >= b for a, b in zip(v, v[1:])):
            raise ValueError("quantile_levels must be strictly increasing")
        return v

    @field_validator("nominal_coverage")
    @classmethod
    def _nominal(cls, v: float) -> float:
        if not (0.0 < v < 1.0):
            raise ValueError("nominal_coverage must be in (0,1)")
        return v

    @model_validator(mode="after")
    def _order(self):
        if self.issue_time > self.valid_start:
            raise ValueError("issue_time must be <= valid_start")
        return self


__all__ = [
    "StrictRecord",
    "round_rearrange_validate",
    "CoverageAttestation",
    "CalibratedBand",
    "BandRequest",
    "ID_RE",
]
