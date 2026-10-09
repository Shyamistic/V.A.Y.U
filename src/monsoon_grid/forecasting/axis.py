"""Regular time axis and the duration->index helper (design §5.0, invariant 6).

Every windowed quantity (lag k, EnbPI block/window, bootstrap block, embargo) is
defined on **axis indices**, never on row indices. This module is the ONLY place
that converts a duration to an axis-index count, so an irregular/sparse issue
grid (EMSx site 8 is 15-min-regular for only ~34% of rows) cannot corrupt lags,
blocks, windows and the embargo at once.

There is NO cross-time fill, ever. A lag whose slot is absent is reported missing.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .errors import InsufficientData


@dataclass(frozen=True)
class RegularAxis:
    """A dataset projected onto a regular axis of its own native step.

    Attributes
    ----------
    step_seconds:
        Native step in seconds (e.g. 900 for 15 min, 3600 for 1 h).
    axis:
        int64 epoch seconds, ``arange(t_min, t_max + step, step)``.
    present_mask:
        bool array, True iff an observation exists at ``axis[i]``.
    values:
        float64 observations aligned to ``axis``; NaN where absent (never filled
        from another time).
    """

    step_seconds: int
    axis: np.ndarray
    present_mask: np.ndarray
    values: np.ndarray

    @property
    def n(self) -> int:
        return int(self.axis.shape[0])

    @property
    def axis_coverage(self) -> float:
        """Fraction of axis slots carrying an observation."""
        return float(self.present_mask.mean()) if self.n else 0.0

    def duration_to_index(self, duration_seconds: int) -> int:
        """Convert a duration to a count of axis steps (exact, must divide)."""
        if duration_seconds % self.step_seconds != 0:
            raise InsufficientData(
                f"duration {duration_seconds}s is not a multiple of native step {self.step_seconds}s"
            )
        return duration_seconds // self.step_seconds


def build_regular_axis(
    timestamps_epoch: np.ndarray,
    values: np.ndarray,
    step_seconds: int,
) -> RegularAxis:
    """Project possibly-irregular (timestamp, value) rows onto a regular axis.

    Parameters
    ----------
    timestamps_epoch:
        int64 epoch-seconds of observations (need not be sorted, must be unique).
    values:
        float observations aligned to ``timestamps_epoch``.
    step_seconds:
        native step in seconds.
    """
    ts = np.asarray(timestamps_epoch, dtype=np.int64)
    vals = np.asarray(values, dtype=np.float64)
    if ts.shape[0] != vals.shape[0]:
        raise InsufficientData("timestamps and values length mismatch")
    if ts.shape[0] == 0:
        raise InsufficientData("cannot build an axis from zero rows")
    if step_seconds <= 0:
        raise InsufficientData("step_seconds must be positive")

    order = np.argsort(ts, kind="stable")
    ts = ts[order]
    vals = vals[order]
    if np.any(np.diff(ts) == 0):
        raise InsufficientData("duplicate timestamps are not allowed on the axis")

    t_min = int(ts[0])
    t_max = int(ts[-1])
    axis = np.arange(t_min, t_max + step_seconds, step_seconds, dtype=np.int64)

    present = np.zeros(axis.shape[0], dtype=bool)
    out_vals = np.full(axis.shape[0], np.nan, dtype=np.float64)

    # Map each observation to its axis index; off-grid timestamps are dropped
    # (they cannot sit on the regular axis without a fill we refuse to make).
    rel = ts - t_min
    on_grid = (rel % step_seconds) == 0
    idx = (rel[on_grid] // step_seconds).astype(np.int64)
    present[idx] = True
    out_vals[idx] = vals[on_grid]

    return RegularAxis(step_seconds=int(step_seconds), axis=axis, present_mask=present, values=out_vals)


def issue_grid_regularity(timestamps_epoch: np.ndarray, step_seconds: int) -> float:
    """Fraction of consecutive issue-time gaps equal to the native step (§5.0).

    Printed beside every metric so a sparse series is never mistaken for a dense
    one. Measured 0.34 for EMSx site 8.
    """
    ts = np.unique(np.asarray(timestamps_epoch, dtype=np.int64))
    if ts.shape[0] < 2:
        return 1.0
    gaps = np.diff(ts)
    return float(np.mean(gaps == step_seconds))


def lag_value(
    ax: RegularAxis,
    target_index: int,
    lag_steps: int,
) -> tuple[float, bool]:
    """Return (value, is_missing) for a lag at axis offset ``lag_steps``.

    ``is_missing`` is True when the lagged slot is off the axis or carries no
    observation. There is never a cross-time fill.
    """
    src = target_index - lag_steps
    if src < 0 or src >= ax.n:
        return (np.nan, True)
    if not ax.present_mask[src]:
        return (np.nan, True)
    return (float(ax.values[src]), False)


def block_coverage(ax: RegularAxis, start_index: int, length: int) -> float:
    """Fraction of present slots inside an axis block [start, start+length)."""
    end = start_index + length
    if start_index < 0 or end > ax.n:
        raise InsufficientData("block out of axis range")
    return float(ax.present_mask[start_index:end].mean())


def sample_blocks(
    ax: RegularAxis,
    block_length: int,
    n_blocks: int,
    rng: np.random.Generator,
    *,
    min_block_coverage: float = 0.75,
    max_block_rejection_ratio: float = 0.5,
) -> tuple[list[int], dict[str, float | int]]:
    """Sample contiguous axis-block start indices with coverage rejection (§5.0).

    Blocks below ``min_block_coverage`` are rejected and resampled; the rejection
    count and acceptance rate are recorded. If rejections exceed
    ``max_block_rejection_ratio`` the run raises :class:`InsufficientData` rather
    than looping forever.
    """
    if block_length <= 0 or block_length > ax.n:
        raise InsufficientData("invalid block length for axis")
    max_start = ax.n - block_length
    accepted: list[int] = []
    rejected = 0
    attempts = 0
    # Allow a bounded number of attempts proportional to the request.
    attempt_cap = max(n_blocks * 20, 50)
    while len(accepted) < n_blocks and attempts < attempt_cap:
        attempts += 1
        start = int(rng.integers(0, max_start + 1))
        if block_coverage(ax, start, block_length) >= min_block_coverage:
            accepted.append(start)
        else:
            rejected += 1
    total = len(accepted) + rejected
    rejection_ratio = (rejected / total) if total else 0.0
    if len(accepted) < n_blocks or rejection_ratio > max_block_rejection_ratio:
        raise InsufficientData(
            f"block rejection ratio {rejection_ratio:.3f} exceeds "
            f"{max_block_rejection_ratio} (accepted {len(accepted)}/{n_blocks})"
        )
    stats: dict[str, float | int] = {
        "rejected": rejected,
        "accepted": len(accepted),
        "acceptance_rate": (len(accepted) / total) if total else 1.0,
    }
    return accepted, stats


__all__ = [
    "RegularAxis",
    "build_regular_axis",
    "issue_grid_regularity",
    "lag_value",
    "block_coverage",
    "sample_blocks",
]
