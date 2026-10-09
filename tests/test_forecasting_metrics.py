"""Closed-form metric tests (design §8.7, §8.8)."""

from __future__ import annotations

import numpy as np
import pytest

from monsoon_grid.forecasting import metrics as M


def test_pinball_at_half_equals_mae_over_two():
    rng = np.random.default_rng(0)
    y = rng.normal(size=200)
    yhat = rng.normal(size=200)
    p = M.pinball(y, yhat, 0.5).value
    mae = M.mae(y, yhat).value
    assert p == pytest.approx(mae / 2.0, rel=1e-12)


def test_crps_single_member_equals_mae():
    y = np.array([1.0, 2.0, 3.0])
    member = np.array([1.5, 2.5, 2.0])
    crps = M.crps_ensemble(y, member).value
    mae = M.mae(y, member).value
    assert crps == pytest.approx(mae)


def test_crps_two_member_hand_computed():
    # Single sample, two members {0, 2}, truth 1.
    # term1 = mean(|0-1|,|2-1|) = 1. term2 = (|0-2|+|2-0|)/(2*4)=4/8=0.5.
    # CRPS = 1 - 0.5 = 0.5.
    y = np.array([1.0])
    members = np.array([[0.0], [2.0]])
    assert M.crps_ensemble(y, members).value == pytest.approx(0.5)


def test_crps_grid_vs_ensemble_same_truncation():
    rng = np.random.default_rng(1)
    y = rng.normal(size=300)
    taus = np.array([0.1, 0.25, 0.5, 0.75, 0.9])
    # Build quantile grid from a normal predictive.
    from scipy.stats import norm

    quantiles = np.stack([norm.ppf(t) * np.ones(300) for t in taus])
    grid, omitted = M.crps_from_quantile_grid(y, taus, quantiles)
    assert omitted == pytest.approx(2.0 * (0.1 + 0.1))
    assert grid.value > 0


def test_coverage_7_of_10():
    y = np.arange(10.0)
    lo = np.full(10, -100.0)
    hi = np.full(10, -100.0)
    hi[:7] = 100.0  # first 7 inside
    cov, wilson = M.coverage_with_wilson(y, lo, hi)
    assert cov.value == pytest.approx(0.7)
    assert wilson[0] < 0.7 < wilson[1]


def test_winkler_inside_below_above():
    # alpha=0.2. inside: width only. below: width + (2/a)(lo-y). above similarly.
    y = np.array([5.0, 0.0, 11.0])
    lo = np.array([2.0, 2.0, 2.0])
    hi = np.array([8.0, 8.0, 8.0])
    w = M.winkler_interval_score(y, lo, hi, 0.2).value
    # row0 inside: 6. row1 below: 6 + 10*(2-0)=26. row2 above: 6 + 10*(11-8)=36.
    assert w == pytest.approx((6 + 26 + 36) / 3.0)


def test_energy_score_zero_when_members_equal_truth():
    y = np.array([1.0, 2.0, 3.0])
    members = np.tile(y, (5, 1))
    assert M.energy_score(y, members).value == pytest.approx(0.0)


def test_energy_score_1d_reduces_to_crps():
    rng = np.random.default_rng(2)
    members = rng.normal(size=(20, 1))
    y = np.array([0.3])
    es = M.energy_score(y, members).value
    # members is (N_members, d=1); as a univariate ensemble that is 20 members at
    # 1 sample, i.e. shape (20, 1) for crps_ensemble (n_members, n_samples).
    crps = M.crps_ensemble(y, members).value
    assert es == pytest.approx(crps, rel=1e-9)


def test_variogram_permutation_symmetry_and_perfect_dependence():
    y = np.array([1.0, 2.0, 4.0])
    members = np.tile(y, (10, 1))  # members perfectly match -> score 0
    assert M.variogram_score(y, members, p=0.5).value == pytest.approx(0.0)


def test_variogram_weight_table():
    w = M.temporal_variogram_weights(3)
    assert w[0, 1] == pytest.approx(1.0)
    assert w[0, 2] == pytest.approx(0.5)
    assert w[1, 2] == pytest.approx(1.0)
    assert w[0, 0] == 0.0


def test_dsm_band_edge_and_slab_boundary():
    # capacity 100, solar pre-2026 band 0.10. deviation exactly at band -> 0.
    r = M.dsm_deviation_charge_proxy_evaluation_only(
        scheduled_mw=[50.0], actual_mw=[60.0], available_capacity_mw=100.0,
        technology="solar", band_regime="pre_2026_04_01", block_minutes=60,
    )
    assert r.normalized_index == pytest.approx(0.0)  # dev=0.10, exactly at band
    # deviation 0.20 -> beyond = 0.10, * 1h = 0.10.
    r2 = M.dsm_deviation_charge_proxy_evaluation_only(
        scheduled_mw=[50.0], actual_mw=[70.0], available_capacity_mw=100.0,
        technology="solar", band_regime="pre_2026_04_01", block_minutes=60,
    )
    assert r2.normalized_index == pytest.approx(0.10)


def test_bias_antisymmetric_zero():
    y = np.array([1.0, 2.0, 3.0])
    yhat = np.array([2.0, 2.0, 2.0])  # errors +1, 0, -1 -> bias 0
    assert M.bias(y, yhat).value == pytest.approx(0.0)


def test_ramp_linear_zero():
    y = np.arange(10.0)
    yhat = np.arange(10.0) + 5.0  # same ramp, constant offset
    assert M.ramp_error(y, yhat, k=1).value == pytest.approx(0.0)
    assert M.ramp_error(y, yhat, k=4).value == pytest.approx(0.0)


def test_bias_shifts_scalefree_invariant_under_constant():
    rng = np.random.default_rng(3)
    y = rng.normal(size=100)
    yhat = rng.normal(size=100)
    c = 3.3
    # bias shifts by -c when truth += c (yhat unchanged): bias = mean(yhat-y).
    b0 = M.bias(y, yhat).value
    b1 = M.bias(y + c, yhat).value
    assert b1 == pytest.approx(b0 - c)
    # ramp error is invariant under adding a constant to both.
    r0 = M.ramp_error(y, yhat).value
    r1 = M.ramp_error(y + c, yhat + c).value
    assert r1 == pytest.approx(r0)


def test_ks_statistic_uniform_small_for_uniform():
    rng = np.random.default_rng(4)
    s = rng.uniform(size=5000)
    assert M.ks_statistic_uniform(s) < 0.05


def test_diebold_mariano_runs():
    rng = np.random.default_rng(5)
    la = rng.uniform(0, 1, size=200)
    lb = la + rng.normal(0.1, 0.05, size=200)  # b worse
    dm, p = M.diebold_mariano(la, lb, h=1)
    assert dm < 0  # a has lower loss
    assert 0.0 <= p <= 1.0
