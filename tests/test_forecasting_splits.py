"""Tests for chronological splits and regime labelling (design §8.10, §6.7)."""

from __future__ import annotations

import numpy as np
import pytest

from monsoon_grid.forecasting.errors import EvaluationProtocolError, InsufficientData
from monsoon_grid.forecasting.splits import (
    CalibrationCellKey,
    EvaluationPassGuard,
    chronological_split,
    horizon_bucket,
    pv_regime,
    wind_regime,
)

STEP_15 = 900


def _axis(n):
    return np.arange(0, n) * STEP_15


def test_split_non_overlap_and_ordering():
    ax = _axis(1000)
    sp = chronological_split(ax, embargo_steps=96)
    blocks = [sp.train, sp.val, sp.calib, sp.test]
    for (_, end), (start, _) in zip(blocks, blocks[1:]):
        assert end + sp.embargo_steps == start  # embargo exactly between blocks
    # non-overlap
    assert sp.train[1] <= sp.val[0]
    assert sp.val[1] <= sp.calib[0]
    assert sp.calib[1] <= sp.test[0]


def test_embargo_width_in_axis_indices():
    ax = _axis(2000)
    sp = chronological_split(ax, embargo_steps=100)
    assert sp.val[0] - sp.train[1] == 100
    assert sp.calib[0] - sp.val[1] == 100
    assert sp.test[0] - sp.calib[1] == 100


def test_no_train_timestamp_ge_any_test_timestamp():
    ax = _axis(1000)
    sp = chronological_split(ax, embargo_steps=96)
    assert ax[sp.train[1] - 1] < ax[sp.test[0]]


def test_split_is_deterministic_under_row_shuffle():
    ax = _axis(1000)
    sp1 = chronological_split(ax, embargo_steps=96)
    # Shuffling the input and re-sorting must not change the (sorted-axis) split.
    rng = np.random.default_rng(0)
    shuffled = ax.copy()
    rng.shuffle(shuffled)
    sp2 = chronological_split(np.sort(shuffled), embargo_steps=96)
    assert (sp1.train, sp1.val, sp1.calib, sp1.test) == (sp2.train, sp2.val, sp2.calib, sp2.test)


def test_split_too_short_raises():
    with pytest.raises(InsufficientData):
        chronological_split(_axis(10), embargo_steps=96)


def test_cell_key_has_no_curtailment_attribute():
    key = CalibrationCellKey(horizon_bucket="h_4step_1h", technology_regime="clear")
    assert not hasattr(key, "curtailment_status")


def test_pv_regime_first_match():
    assert pv_regime(0.3, month=1) == "cloudy_monsoon"
    assert pv_regime(0.55, month=7) == "cloudy_monsoon"  # monsoon + <0.60
    assert pv_regime(0.55, month=1) == "intermediate"  # not monsoon
    assert pv_regime(0.8, month=1) == "clear"


def test_wind_regime_first_match():
    v_ci, v_rated, v_co = 3.0, 12.0, 25.0
    assert wind_regime(np.full(10, 26.0), v_ci=v_ci, v_rated=v_rated, v_co=v_co) == "above_cutout"
    assert wind_regime(np.full(10, 13.0), v_ci=v_ci, v_rated=v_rated, v_co=v_co) == "high_wind"
    assert wind_regime(np.full(10, 1.0), v_ci=v_ci, v_rated=v_rated, v_co=v_co) == "low"
    assert wind_regime(np.full(10, 6.0), v_ci=v_ci, v_rated=v_rated, v_co=v_co) == "operating"


def test_horizon_bucket_labels():
    # 15-min native step. lead 60 min = 4 steps -> bucket h_4step_1h.
    assert horizon_bucket(60, 15) == "h_4step_1h"
    assert horizon_bucket(15, 15) == "h_1step_0.25h"
    assert horizon_bucket(24 * 60, 15) == "h_96step_24h"


def test_test_block_access_guard():
    guard = EvaluationPassGuard()
    guard.record_evaluation_pass()
    assert guard.count == 1
    with pytest.raises(EvaluationProtocolError):
        guard.record_evaluation_pass()
