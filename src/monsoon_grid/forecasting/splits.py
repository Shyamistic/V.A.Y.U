"""Chronological splits, embargo and regime labelling (design §8.10, §6.7).

Four contiguous, strictly time-ordered blocks (train/val/calib/test = 60/10/10/20)
with an embargo gap between each, measured in axis indices. Never shuffled, never
random-fold cross-validated.

Two DISTINCT key types (invariant 3):
- ``calibration_cell_key = (horizon_bucket, technology_regime)`` -- issue-time
  computable only; has NO curtailment field, so an illegal key is unrepresentable.
- ``report_slice_key = calibration_cell_key + (curtailment_status,)`` -- reporting
  and training-exclusion only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from .errors import EvaluationProtocolError, InsufficientData

PvRegime = Literal["cloudy_monsoon", "clear", "intermediate"]
WindRegime = Literal["above_cutout", "high_wind", "low", "operating"]

MONSOON_MONTHS = frozenset({6, 7, 8, 9})  # Jun-Sep (assumption)


@dataclass(frozen=True)
class CalibrationCellKey:
    """Issue-time-computable cell key. No curtailment field by construction."""

    horizon_bucket: str
    technology_regime: str

    def as_str(self) -> str:
        return f"{self.horizon_bucket}|{self.technology_regime}"


@dataclass(frozen=True)
class ReportSliceKey:
    """Reporting/exclusion key: cell key + verification-side curtailment flag."""

    cell: CalibrationCellKey
    curtailment_status: str  # curtailed | not_curtailed | not_identifiable

    def as_str(self) -> str:
        return f"{self.cell.as_str()}|{self.curtailment_status}"


@dataclass(frozen=True)
class Split:
    """Index ranges on the regular axis for each contiguous block."""

    train: tuple[int, int]
    val: tuple[int, int]
    calib: tuple[int, int]
    test: tuple[int, int]
    embargo_steps: int
    boundaries_epoch: dict[str, int]

    def block(self, name: str) -> tuple[int, int]:
        return getattr(self, name)


def chronological_split(
    axis_epoch: np.ndarray,
    *,
    embargo_steps: int,
    fractions: tuple[float, float, float, float] = (0.6, 0.1, 0.1, 0.2),
) -> Split:
    """Build train/val/calib/test index ranges with embargo gaps (§8.10).

    Boundaries are computed from the data range only (no peeking at targets).
    """
    axis_epoch = np.asarray(axis_epoch, dtype=np.int64)
    n = axis_epoch.shape[0]
    if abs(sum(fractions) - 1.0) > 1e-9:
        raise InsufficientData("split fractions must sum to 1")
    if embargo_steps < 0:
        raise InsufficientData("embargo_steps must be >= 0")
    # Three embargo gaps consume indices.
    usable = n - 3 * embargo_steps
    if usable < 4:
        raise InsufficientData(f"axis too short for a 4-block split with embargo (n={n})")

    n_train = int(usable * fractions[0])
    n_val = int(usable * fractions[1])
    n_calib = int(usable * fractions[2])
    # test gets the remainder of usable.
    cursor = 0
    train = (cursor, cursor + n_train)
    cursor = train[1] + embargo_steps
    val = (cursor, cursor + n_val)
    cursor = val[1] + embargo_steps
    calib = (cursor, cursor + n_calib)
    cursor = calib[1] + embargo_steps
    test = (cursor, n)

    boundaries = {
        "train_start": int(axis_epoch[train[0]]),
        "train_end": int(axis_epoch[train[1] - 1]),
        "val_start": int(axis_epoch[val[0]]),
        "val_end": int(axis_epoch[val[1] - 1]),
        "calib_start": int(axis_epoch[calib[0]]),
        "calib_end": int(axis_epoch[calib[1] - 1]),
        "test_start": int(axis_epoch[test[0]]),
        "test_end": int(axis_epoch[test[1] - 1]),
    }
    return Split(train=train, val=val, calib=calib, test=test, embargo_steps=embargo_steps, boundaries_epoch=boundaries)


# --------------------------------------------------------------------------- regimes (§6.7)


def pv_regime(daily_mean_kt: float, month: int) -> PvRegime:
    """First-match precedence (§6.7)."""
    is_monsoon = month in MONSOON_MONTHS
    if daily_mean_kt < 0.45 or (is_monsoon and daily_mean_kt < 0.60):
        return "cloudy_monsoon"
    if daily_mean_kt >= 0.70:
        return "clear"
    return "intermediate"


def wind_regime(
    hub_speeds_day: np.ndarray,
    *,
    v_ci: float,
    v_rated: float,
    v_co: float,
    fraction: float = 0.25,
) -> WindRegime:
    """First-match precedence (§6.7). Computed on a day's valid slots."""
    s = np.asarray(hub_speeds_day, dtype=np.float64)
    n = s.shape[0]
    if n == 0:
        return "operating"
    if np.mean(s >= v_co) >= fraction:
        return "above_cutout"
    if np.mean(s >= v_rated) >= fraction:
        return "high_wind"
    if float(np.mean(s)) < v_ci:
        return "low"
    return "operating"


def horizon_bucket(lead_minutes: int, native_step_minutes: int) -> str:
    """Map a lead time to a nominal axis-step bucket, labelled in hours (§8.12)."""
    steps = lead_minutes // native_step_minutes
    for b in (1, 2, 4, 8, 16, 32, 96):
        if steps <= b:
            hours = b * native_step_minutes / 60.0
            return f"h_{b}step_{hours:g}h"
    hours = steps * native_step_minutes / 60.0
    return f"h_{steps}step_{hours:g}h"


class EvaluationPassGuard:
    """Counts EVALUATION passes over the test block, not score arrivals (§8.10).

    A value > 1 within a run is a hard EvaluationProtocolError. Streaming online
    calibration updates inside the test block are NOT counted.
    """

    def __init__(self) -> None:
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    def record_evaluation_pass(self) -> None:
        self._count += 1
        if self._count > 1:
            raise EvaluationProtocolError(f"test block evaluated {self._count} times (must be exactly 1)")


__all__ = [
    "PvRegime",
    "WindRegime",
    "CalibrationCellKey",
    "ReportSliceKey",
    "Split",
    "chronological_split",
    "pv_regime",
    "wind_regime",
    "horizon_bucket",
    "EvaluationPassGuard",
    "MONSOON_MONTHS",
]
