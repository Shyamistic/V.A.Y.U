"""Tests for the regular time axis (design §5.0, invariant 6)."""

from __future__ import annotations

import numpy as np
import pytest

from monsoon_grid.forecasting.axis import (
    build_regular_axis,
    issue_grid_regularity,
    lag_value,
    sample_blocks,
)
from monsoon_grid.forecasting.errors import InsufficientData

STEP_15 = 900
STEP_1H = 3600


def test_irregular_input_present_mask():
    # Native 15-min; rows at 0, 900, 2700 (skip 1800) => present at idx 0,1,3.
    ts = np.array([0, 900, 2700], dtype=np.int64)
    vals = np.array([10.0, 11.0, 13.0])
    ax = build_regular_axis(ts, vals, STEP_15)
    assert ax.n == 4  # 0,900,1800,2700
    assert ax.present_mask.tolist() == [True, True, False, True]
    assert np.isnan(ax.values[2])
    assert ax.values[3] == 13.0


def test_lag_on_absent_slot_is_missing_not_crossfilled():
    ts = np.array([0, 900, 2700], dtype=np.int64)
    vals = np.array([10.0, 11.0, 13.0])
    ax = build_regular_axis(ts, vals, STEP_15)
    # target idx 3 (t=2700), lag 1 step -> idx 2 which is absent.
    val, missing = lag_value(ax, 3, 1)
    assert missing is True
    assert np.isnan(val)
    # lag 3 steps -> idx 0 present.
    val2, missing2 = lag_value(ax, 3, 3)
    assert missing2 is False
    assert val2 == 10.0
    # lag beyond start -> missing.
    _, m3 = lag_value(ax, 0, 1)
    assert m3 is True


def test_duration_to_index_exact_at_15min_and_1h():
    ax15 = build_regular_axis(np.array([0, 900]), np.array([1.0, 2.0]), STEP_15)
    ax1h = build_regular_axis(np.array([0, 3600]), np.array([1.0, 2.0]), STEP_1H)
    assert ax15.duration_to_index(24 * 3600) == 96
    assert ax1h.duration_to_index(24 * 3600) == 24
    assert ax15.duration_to_index(2 * 3600) == 8
    assert ax1h.duration_to_index(2 * 3600) == 2


def test_duration_not_multiple_raises():
    ax1h = build_regular_axis(np.array([0, 3600]), np.array([1.0, 2.0]), STEP_1H)
    with pytest.raises(InsufficientData):
        ax1h.duration_to_index(900)  # 15 min not a multiple of 1 h


def test_issue_grid_regularity_hand_histogram():
    # 10 rows: gaps mostly 900 with some 1800. regularity = fraction gaps==900.
    ts = np.array([0, 900, 1800, 3600, 4500, 5400, 7200, 8100, 9000, 9900], dtype=np.int64)
    reg = issue_grid_regularity(ts, STEP_15)
    gaps = np.diff(ts)
    assert reg == pytest.approx(np.mean(gaps == 900))


def test_issue_grid_regularity_emsx_like_034():
    # Build a gap pattern that lands at ~0.34 regularity.
    rng = np.random.default_rng(0)
    gaps = []
    for _ in range(1000):
        if rng.random() < 0.34:
            gaps.append(900)
        else:
            gaps.append(900 * int(rng.integers(2, 11)))
    ts = np.concatenate([[0], np.cumsum(gaps)]).astype(np.int64)
    reg = issue_grid_regularity(ts, STEP_15)
    assert 0.28 <= reg <= 0.40


def test_projection_idempotence():
    rng = np.random.default_rng(1)
    base = np.arange(0, 100) * STEP_15
    keep = rng.random(100) < 0.6
    ts = base[keep]
    vals = rng.normal(size=ts.shape[0])
    ax1 = build_regular_axis(ts, vals, STEP_15)
    # Reproject the present rows -> identical axis.
    present_ts = ax1.axis[ax1.present_mask]
    present_vals = ax1.values[ax1.present_mask]
    ax2 = build_regular_axis(present_ts, present_vals, STEP_15)
    assert np.array_equal(ax1.axis, ax2.axis)
    assert np.array_equal(ax1.present_mask, ax2.present_mask)
    np.testing.assert_array_equal(np.nan_to_num(ax1.values), np.nan_to_num(ax2.values))


def test_duplicate_timestamps_rejected():
    with pytest.raises(InsufficientData):
        build_regular_axis(np.array([0, 0]), np.array([1.0, 2.0]), STEP_15)


def test_sample_blocks_rejects_low_coverage():
    # A dense axis: all present -> acceptance easy.
    ts = np.arange(0, 200) * STEP_15
    vals = np.ones(200)
    ax = build_regular_axis(ts, vals, STEP_15)
    rng = np.random.default_rng(42)
    starts, stats = sample_blocks(ax, block_length=8, n_blocks=5, rng=rng)
    assert len(starts) == 5
    assert stats["acceptance_rate"] == 1.0


def test_sample_blocks_too_sparse_raises():
    # Mostly absent axis -> rejection ratio too high.
    ts = np.array([0, 900 * 50, 900 * 100, 900 * 150], dtype=np.int64)
    vals = np.ones(4)
    ax = build_regular_axis(ts, vals, STEP_15)
    rng = np.random.default_rng(42)
    with pytest.raises(InsufficientData):
        sample_blocks(ax, block_length=8, n_blocks=5, rng=rng, min_block_coverage=0.75)
