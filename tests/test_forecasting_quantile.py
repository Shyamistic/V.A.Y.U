"""Tests for residual quantile models (design §5.3)."""

from __future__ import annotations

import numpy as np
import pytest

from monsoon_grid.forecasting.quantile import (
    clip_band,
    fit_gbm_quantile,
    fit_linear_quantile,
    fit_linear_quantile_model,
    pinball_loss,
    predict_linear,
    rearrange,
)


def test_lp_median_regression_recovers_known_line():
    # y = 2x + 1 with symmetric noise -> tau=0.5 recovers slope 2, intercept 1.
    rng = np.random.default_rng(0)
    x = np.linspace(0, 10, 200).reshape(-1, 1)
    y = (2.0 * x[:, 0] + 1.0) + rng.normal(0, 0.5, size=200)
    beta = fit_linear_quantile(x, y, 0.5)
    # beta = [slope, intercept]
    assert beta[0] == pytest.approx(2.0, abs=0.2)
    assert beta[1] == pytest.approx(1.0, abs=0.3)


def test_lp_quantiles_order_increasing_on_average():
    rng = np.random.default_rng(1)
    x = rng.uniform(0, 1, size=(300, 2))
    y = x[:, 0] + rng.normal(0, 0.3, size=300)
    model = fit_linear_quantile_model(x, y, taus=(0.1, 0.5, 0.9))
    pred = model.predict(x)  # (3, 300), rearranged
    assert np.all(np.diff(pred, axis=0) >= -1e-9)


def test_gbm_determinism_across_repeated_fits():
    rng = np.random.default_rng(2)
    x = rng.uniform(0, 1, size=(400, 3))
    y = np.sin(3 * x[:, 0]) + rng.normal(0, 0.1, size=400)
    m1 = fit_gbm_quantile(x, y, 0.5, n_rounds=20, min_leaf=20)
    m2 = fit_gbm_quantile(x, y, 0.5, n_rounds=20, min_leaf=20)
    p1 = m1.predict(x)
    p2 = m2.predict(x)
    np.testing.assert_array_equal(p1, p2)


def test_gbm_tracks_monotone_signal():
    rng = np.random.default_rng(3)
    x = np.linspace(0, 1, 500).reshape(-1, 1)
    y = 5.0 * x[:, 0] + rng.normal(0, 0.1, size=500)
    m = fit_gbm_quantile(x, y, 0.5, n_rounds=40, min_leaf=20)
    pred = m.predict(x)
    # On average prediction should increase with x.
    assert np.corrcoef(x[:, 0], pred)[0, 1] > 0.9


def test_rearrangement_monotone_and_loss_non_increase():
    rng = np.random.default_rng(4)
    # Deliberately crossed quantiles.
    q = rng.normal(0, 1, size=(5, 100))
    qr = rearrange(q)
    assert np.all(np.diff(qr, axis=0) >= -1e-12)
    # Aggregate pinball loss after rearrangement <= before (Chernozhukov et al.).
    y = rng.normal(0, 1, size=100)
    taus = [0.1, 0.3, 0.5, 0.7, 0.9]
    before = sum(pinball_loss(y, q[i], taus[i]) for i in range(5))
    after = sum(pinball_loss(y, qr[i], taus[i]) for i in range(5))
    assert after <= before + 1e-9


def test_clip_order_predict_rearrange_clip():
    # Order of operations: predict -> rearrange -> clip (§5.1).
    raw = np.array([[0.9, -0.1], [0.1, 0.5], [-0.3, 0.8]])  # crossed
    rearranged = rearrange(raw)
    clipped = clip_band(rearranged, 0.0, 1.0)
    assert np.all(clipped >= 0.0) and np.all(clipped <= 1.0)
    assert np.all(np.diff(clipped, axis=0) >= -1e-12)


def test_predict_linear_affine():
    beta = np.array([2.0, 1.0])  # slope, intercept
    x = np.array([[0.0], [1.0], [2.0]])
    np.testing.assert_allclose(predict_linear(x, beta), [1.0, 3.0, 5.0])
