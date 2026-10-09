"""Tests for scenario generation (design §7)."""

from __future__ import annotations

import numpy as np
import pytest

from monsoon_grid.config import PSD_TOLERANCE as PRODUCT_PSD_TOLERANCE
from monsoon_grid.forecasting import scenarios as S


def test_psd_tolerance_matches_product():
    assert S.PSD_TOLERANCE == PRODUCT_PSD_TOLERANCE


def test_spearman_to_pearson_identity_at_one():
    # rho_S=1 -> 2 sin(pi/6)=2*0.5=1.
    assert S.spearman_to_pearson(np.array([1.0]))[0] == pytest.approx(1.0)
    assert S.spearman_to_pearson(np.array([0.0]))[0] == pytest.approx(0.0)


def test_block_bootstrap_index_determinism():
    rng1 = np.random.default_rng(7)
    rng2 = np.random.default_rng(7)
    i1 = S.moving_block_bootstrap_indices(100, 8, 96, rng1)
    i2 = S.moving_block_bootstrap_indices(100, 8, 96, rng2)
    np.testing.assert_array_equal(i1, i2)
    assert i1.shape[0] == 96


def test_kronecker_assembly_shape_and_separability_departure():
    r_cross = np.array([[1.0, 0.5], [0.5, 1.0]])
    t = S.temporal_decay_matrix(3, tau_hat=2.0)
    sigma = S.kronecker_separable(r_cross, t)
    assert sigma.shape == (6, 6)
    # A perfectly separable empirical equals the Kronecker -> 0 departure.
    assert S.separability_departure(sigma, sigma) == pytest.approx(0.0)
    # A perturbed empirical -> positive departure.
    pert = sigma.copy()
    pert[0, 5] += 0.3
    assert S.separability_departure(pert, sigma) > 0.0


def test_higham_yields_psd_unit_diagonal():
    # Start from a non-PSD "correlation-like" matrix.
    a = np.array([[1.0, 0.9, -0.9], [0.9, 1.0, 0.9], [-0.9, 0.9, 1.0]])
    c = S.higham_nearest_correlation(a)
    np.testing.assert_allclose(np.diag(c), 1.0, atol=1e-12)
    min_eig = float(np.min(np.linalg.eigvalsh(c)))
    assert min_eig >= PRODUCT_PSD_TOLERANCE


def test_reduction_weights_sum_to_one_and_k_paths():
    rng = np.random.default_rng(1)
    paths = rng.normal(size=(50, 10))
    sel, probs = S.fast_forward_reduction(paths, 5)
    assert sel.shape[0] == 5
    assert probs.sum() == pytest.approx(1.0, abs=1e-9)
    assert np.all(probs >= 0.0)


def test_reduction_k_gt_n_raises():
    paths = np.zeros((3, 4))
    with pytest.raises(S.ScenarioError):
        S.fast_forward_reduction(paths, 5)


def test_reduction_l2_distance_hand_computed():
    # Three identical-ish paths; two clusters. K=2 should pick representatives.
    paths = np.array([[0.0, 0.0], [0.1, 0.0], [10.0, 10.0]])
    sel, probs = S.fast_forward_reduction(paths, 2)
    assert len(sel) == 2
    assert probs.sum() == pytest.approx(1.0)


def test_independent_sampling_understates_joint_tail():
    # Positively-correlated PV sites: shared cloud field. Build dependent and
    # independent aggregates from identical marginals and the same seed.
    rng = np.random.default_rng(123)
    n = 20000
    d = 5
    cap = 100.0  # MW per site
    # Common factor (shared cloud) + idiosyncratic noise, each in [0,1].
    common = rng.normal(0, 1, size=n)
    # Dependent: each asset = clip(mean + rho*common + noise).
    rho = 0.9
    dep_assets = np.clip(
        0.5 + 0.15 * (rho * common[:, None] + np.sqrt(1 - rho**2) * rng.normal(0, 1, size=(n, d))),
        0, 1,
    )
    dep_portfolio = dep_assets.sum(axis=1) * cap
    # Independent: shuffle each asset column independently to destroy dependence
    # while preserving each marginal exactly.
    ind_assets = dep_assets.copy()
    for j in range(d):
        ind_assets[:, j] = rng.permutation(ind_assets[:, j])
    ind_portfolio = ind_assets.sum(axis=1) * cap

    cmp = S.independence_understates_tail(dep_portfolio, ind_portfolio, tail_margin_mw=0.5)
    # Independent sampling narrows the aggregate and raises the 5th percentile.
    assert cmp.understates is True
    assert cmp.p05_independent_mw > cmp.p05_dependent_mw
    # Dependent tail is heavier (lower ES).
    assert cmp.es05_dependent_mw < cmp.es05_independent_mw


def test_gaussian_copula_sample_shape_and_range():
    rng = np.random.default_rng(9)
    corr = np.array([[1.0, 0.4], [0.4, 1.0]])
    u = S.gaussian_copula_sample(corr, 1000, rng)
    assert u.shape == (1000, 2)
    assert np.all((u > 0) & (u < 1))
    # Recovered Spearman correlation should be positive.
    assert np.corrcoef(u, rowvar=False)[0, 1] > 0.2
