"""Tests for the leakage-safe feature builder (design §5.2, invariant 1)."""

from __future__ import annotations

import numpy as np

from monsoon_grid.forecasting.axis import build_regular_axis
from monsoon_grid.forecasting.features import build_features

STEP_15 = 900


def _obs_axis(n=200, seed=0):
    rng = np.random.default_rng(seed)
    ts = np.arange(0, n) * STEP_15
    vals = rng.uniform(0, 1, size=n)
    return build_regular_axis(ts, vals, STEP_15), ts, vals


def test_future_poisoning_does_not_change_features():
    ax, ts, vals = _obs_axis()
    issue = int(ts[100])
    valid = ts[101:105].astype(np.float64)
    nwp = {"ghi": np.array([500.0, 480.0, 460.0, 440.0])}
    fm1 = build_features(issue, valid, ax, nwp_forecast=nwp)

    # Poison every observation strictly after issue_time.
    poisoned_vals = vals.copy()
    future = ts > issue
    poisoned_vals[future] = 9999.0
    ax2 = build_regular_axis(ts, poisoned_vals, STEP_15)
    fm2 = build_features(issue, valid, ax2, nwp_forecast=nwp)

    assert fm1.names == fm2.names
    np.testing.assert_array_equal(fm1.matrix, fm2.matrix)


def test_missing_lag_is_indicator_plus_fill_not_forward_filled():
    # Build an axis with a gap right before the issue time.
    ts = np.array([0, 900, 1800, 4500], dtype=np.int64)  # gap over 2700, 3600
    vals = np.array([0.1, 0.2, 0.3, 0.4])
    ax = build_regular_axis(ts, vals, STEP_15)
    issue = 4500  # last present slot is idx of t=4500 (idx 5)
    valid = np.array([5400.0])
    fm = build_features(issue, valid, ax, publication_lag_steps=1, lag_set=(1, 2, 4))
    # publication lag 1 -> cutoff at 3600 (idx 4, absent); newest admissible is
    # t=1800 (idx 2). lag_1 -> offset 0 -> idx2 present (0.3).
    assert fm.column("lag_1_is_missing")[0] == 0.0
    assert fm.column("lag_1")[0] == 0.3
    # lag_4 -> offset 3 -> idx -1 (before start) -> missing with neutral fill,
    # never a value carried across the gap.
    assert fm.column("lag_4_is_missing")[0] == 1.0
    assert fm.column("lag_4")[0] == 0.0


def test_nwp_missing_companion_indicator():
    ax, ts, _ = _obs_axis()
    issue = int(ts[50])
    valid = ts[51:53].astype(np.float64)
    nwp = {"temp": np.array([30.0, np.nan])}
    fm = build_features(issue, valid, ax, nwp_forecast=nwp)
    assert fm.column("temp_is_missing").tolist() == [0.0, 1.0]
    assert fm.column("temp")[1] == 0.0  # neutral fill, not NaN


def test_feature_matrix_shape():
    ax, ts, _ = _obs_axis()
    issue = int(ts[80])
    valid = ts[81:89].astype(np.float64)
    fm = build_features(issue, valid, ax)
    assert fm.matrix.shape[0] == 8
    assert fm.matrix.shape[1] == len(fm.names)
    assert np.all(np.isfinite(fm.matrix))
