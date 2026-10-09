"""Tests for calibration: CQR, ACI, EnbPI LOO, admissibility (design §6)."""

from __future__ import annotations

import numpy as np
import pytest

from monsoon_grid.forecasting.calibration import (
    aci_clip_bounds,
    aci_coverage_bound,
    admissible_mask,
    apply_cqr,
    assert_admissible,
    conformal_quantile,
    cqr_score,
    empirical_coverage,
    enbpi_loo_aggregate,
    make_aci_state,
)
from monsoon_grid.forecasting.errors import EvaluationProtocolError


def test_cqr_score_hand_computed():
    # y below lo -> lo - y positive; y above hi -> y - hi; inside -> negative max.
    y = np.array([1.0, 5.0, 11.0])
    lo = np.array([2.0, 2.0, 2.0])
    hi = np.array([8.0, 8.0, 8.0])
    s = cqr_score(y, lo, hi)
    assert s[0] == pytest.approx(1.0)  # 2-1
    assert s[1] == pytest.approx(-3.0)  # inside: max(2-5, 5-8) = -3
    assert s[2] == pytest.approx(3.0)  # 11-8


def test_conformal_quantile_rank():
    scores = np.array([0.1, 0.2, 0.3, 0.4, 0.5])
    # alpha=0.2 -> rank = ceil(6*0.8)=5 -> s[4]=0.5
    assert conformal_quantile(scores, 0.2) == pytest.approx(0.5)
    # alpha=0.5 -> rank=ceil(6*0.5)=3 -> s[2]=0.3
    assert conformal_quantile(scores, 0.5) == pytest.approx(0.3)


def test_apply_cqr_on_clipped_band_improves_coverage():
    rng = np.random.default_rng(0)
    y = rng.normal(0, 1, size=1000)
    lo = np.full(1000, -0.5)  # too narrow
    hi = np.full(1000, 0.5)
    scores = cqr_score(y, lo, hi)
    new_lo, new_hi = apply_cqr(lo, hi, scores, 0.2, min_width=0.01, clip_lo=-10, clip_hi=10)
    cov = empirical_coverage(y, new_lo, new_hi)
    assert cov >= 0.75  # near nominal 0.8


def test_apply_cqr_width_floor():
    lo = np.zeros(10)
    hi = np.zeros(10)
    scores = np.zeros(10)
    new_lo, new_hi = apply_cqr(lo, hi, scores, 0.2, min_width=0.5, clip_lo=-10, clip_hi=10)
    assert np.all((new_hi - new_lo) >= 0.5 - 1e-9)


def test_aci_clip_bounds_two_sided():
    assert aci_clip_bounds(0.10) == (pytest.approx(0.025), pytest.approx(0.30))
    assert aci_clip_bounds(0.20) == (pytest.approx(0.05), pytest.approx(0.60))
    lo, hi = aci_clip_bounds(0.50)
    assert lo == pytest.approx(0.125)
    assert hi == pytest.approx(0.90)
    # Both bounds strictly two-sided around nominal.
    assert lo < 0.50 < hi


def test_aci_recursion_step_by_step():
    st = make_aci_state(0.20, gamma=0.1)
    # Start at alpha_t = 0.20. A miss (err=1): alpha_{t+1}=0.2+0.1*(0.2-1)=0.12.
    a1 = st.update(covered=False)
    assert a1 == pytest.approx(0.12)
    # A hit (err=0): 0.12 + 0.1*(0.2-0)=0.14.
    a2 = st.update(covered=True)
    assert a2 == pytest.approx(0.14)


def test_aci_coverage_bound_numerator():
    # bound = (max(clip_hi, 1-clip_lo) + gamma) / (T*gamma)
    b = aci_coverage_bound(0.20, 0.01, 1000)
    lo, hi = aci_clip_bounds(0.20)
    expected = (max(hi, 1 - lo) + 0.01) / (1000 * 0.01)
    assert b == pytest.approx(expected)


def test_enbpi_loo_excludes_members_that_saw():
    # 3 members, 2 timestamps. For ts0, member0 saw it -> excluded.
    preds = np.array([[10.0, 1.0], [20.0, 2.0], [30.0, 3.0]])
    saw = np.array([[True, False], [False, False], [False, True]])
    out = enbpi_loo_aggregate(preds, saw, phi="median")
    # ts0: members 1,2 -> median(20,30)=25.
    assert out[0] == pytest.approx(25.0)
    # ts1: members 0,1 -> median(1,2)=1.5.
    assert out[1] == pytest.approx(1.5)


def test_admissibility_filter():
    vts = np.array([100, 200, 300], dtype=np.int64)
    issue = 250
    lag = 10
    mask = admissible_mask(vts, issue, lag)
    # 100+10<=250 True; 200+10<=250 True; 300+10<=250 False.
    assert mask.tolist() == [True, True, False]


def test_assert_admissible_raises_on_future_score():
    vts = np.array([100, 300], dtype=np.int64)
    with pytest.raises(EvaluationProtocolError):
        assert_admissible(vts, issue_time=250, publication_lag_seconds=10)
    # all admissible -> no raise
    assert_admissible(np.array([100, 200]), issue_time=250, publication_lag_seconds=10)
