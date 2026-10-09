"""Part C2 -- metrics with closed-form tests (design §8.7, §8.8).

Every function takes an explicit evaluation ``mask`` so all methods are scored on
identical rows (§8.9). Results are frozen dataclasses carrying ``value``/``n``/
``unit``/``normalization`` so a bare float can never be reported without its
denominator.

References (no novelty claimed): Koenker & Bassett (1978) pinball; Gneiting &
Raftery (2007) CRPS/energy/interval scores; Hersbach (2000) CRPS; Scheuerer &
Hamill (2015) variogram score; Diebold & Mariano (1995) + Harvey, Leybourne &
Newbold (1997).

The DSM proxy is EVALUATION-ONLY: the authoritative calculator is
``src/monsoon_grid/dsm.py`` (another workflow's file) and this proxy must NOT be
imported by production code (invariant 13).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, Sequence

import numpy as np


@dataclass(frozen=True)
class MetricValue:
    value: float
    n: int
    unit: str
    normalization: str


def _apply_mask(mask, *arrays):
    if mask is None:
        return arrays
    mask = np.asarray(mask, dtype=bool)
    return tuple(np.asarray(a)[mask] for a in arrays)


# ===========================================================================
# Point metrics
# ===========================================================================


def mae(y, yhat, mask=None, *, capacity=None, capacity_source="unknown") -> MetricValue:
    y, yhat = _apply_mask(mask, y, yhat)
    val = float(np.mean(np.abs(np.asarray(y, float) - np.asarray(yhat, float))))
    return MetricValue(val, len(y), "MW", "none")


def rmse(y, yhat, mask=None) -> MetricValue:
    y, yhat = _apply_mask(mask, y, yhat)
    val = float(np.sqrt(np.mean((np.asarray(y, float) - np.asarray(yhat, float)) ** 2)))
    return MetricValue(val, len(y), "MW", "none")


def nmae(y, yhat, capacity, capacity_source, mask=None) -> MetricValue:
    y, yhat = _apply_mask(mask, y, yhat)
    val = float(np.mean(np.abs(np.asarray(y, float) - np.asarray(yhat, float)))) / capacity
    return MetricValue(val, len(y), "fraction", f"capacity={capacity}:{capacity_source}")


def bias(y, yhat, mask=None) -> MetricValue:
    y, yhat = _apply_mask(mask, y, yhat)
    val = float(np.mean(np.asarray(yhat, float) - np.asarray(y, float)))
    return MetricValue(val, len(y), "MW", "none")


def ramp_error(y, yhat, k=1, mask=None) -> MetricValue:
    y = np.asarray(y, float)
    yhat = np.asarray(yhat, float)
    # k-step ramp error: MAE of (delta_k yhat - delta_k y).
    dy = y[k:] - y[:-k]
    dyhat = yhat[k:] - yhat[:-k]
    val = float(np.mean(np.abs(dyhat - dy)))
    return MetricValue(val, len(dy), "MW", f"k={k}")


# ===========================================================================
# Probabilistic metrics
# ===========================================================================


def pinball(y, pred_tau, tau, mask=None) -> MetricValue:
    y, pred_tau = _apply_mask(mask, y, pred_tau)
    e = np.asarray(y, float) - np.asarray(pred_tau, float)
    val = float(np.mean(np.maximum(tau * e, (tau - 1.0) * e)))
    return MetricValue(val, len(y), "MW", f"tau={tau}")


def crps_ensemble(y, members, mask=None) -> MetricValue:
    """CRPS from an ensemble (Gneiting & Raftery 2007; Hersbach 2000).

    ``members`` is (n_members, n) or (n,) for a single member. Per-sample
    CRPS = mean|x_i - y| - 0.5*mean|x_i - x_j|, averaged over samples.
    """
    members = np.atleast_2d(np.asarray(members, float))
    y = np.asarray(y, float)
    if mask is not None:
        m = np.asarray(mask, bool)
        members = members[:, m]
        y = y[m]
    n_mem = members.shape[0]
    term1 = np.mean(np.abs(members - y[None, :]), axis=0)
    # pairwise term
    diff = np.abs(members[:, None, :] - members[None, :, :])
    term2 = diff.sum(axis=(0, 1)) / (2.0 * n_mem * n_mem)
    val = float(np.mean(term1 - term2))
    return MetricValue(val, y.shape[0], "MW", f"members={n_mem}")


def crps_from_quantile_grid(y, taus, quantiles, mask=None) -> tuple[MetricValue, float]:
    """Truncated CRPS = 2 * integral(pinball dtau) over the grid, trapezoid, plus
    a reported omitted-tail mass (§8.7). ``quantiles`` is (n_levels, n)."""
    taus = np.asarray(taus, float)
    quantiles = np.asarray(quantiles, float)
    y = np.asarray(y, float)
    if mask is not None:
        m = np.asarray(mask, bool)
        quantiles = quantiles[:, m]
        y = y[m]
    pin = np.array([pinball(y, quantiles[i], taus[i]).value for i in range(len(taus))])
    trunc = 2.0 * float(np.trapezoid(pin, taus))
    omitted = 2.0 * (taus[0] + (1.0 - taus[-1]))
    return MetricValue(trunc, y.shape[0], "MW", f"truncated_mass_omitted={omitted:.4f}"), omitted


def coverage_with_wilson(y, lo, hi, mask=None, z=1.959963984540054) -> tuple[MetricValue, tuple[float, float]]:
    """Empirical coverage with a Wilson binomial interval (always with width)."""
    y, lo, hi = _apply_mask(mask, y, lo, hi)
    y = np.asarray(y, float)
    inside = (np.asarray(lo, float) <= y) & (y <= np.asarray(hi, float))
    n = inside.shape[0]
    p = float(np.mean(inside)) if n else 0.0
    if n == 0:
        return MetricValue(0.0, 0, "fraction", "coverage"), (0.0, 0.0)
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return MetricValue(p, n, "fraction", "coverage"), (center - half, center + half)


def sharpness(lo, hi, mask=None) -> MetricValue:
    lo, hi = _apply_mask(mask, lo, hi)
    val = float(np.mean(np.asarray(hi, float) - np.asarray(lo, float)))
    return MetricValue(val, len(lo), "MW", "mean_width")


def winkler_interval_score(y, lo, hi, alpha, mask=None) -> MetricValue:
    """Interval (Winkler) score (Gneiting & Raftery 2007)."""
    y, lo, hi = _apply_mask(mask, y, lo, hi)
    y = np.asarray(y, float)
    lo = np.asarray(lo, float)
    hi = np.asarray(hi, float)
    width = hi - lo
    below = (2.0 / alpha) * np.maximum(lo - y, 0.0)
    above = (2.0 / alpha) * np.maximum(y - hi, 0.0)
    val = float(np.mean(width + below + above))
    return MetricValue(val, len(y), "MW", f"alpha={alpha}")


def pit_values(y, taus, quantiles, rng, mask=None) -> np.ndarray:
    """PIT by monotone interpolation of the quantile grid, randomized on ties."""
    taus = np.asarray(taus, float)
    quantiles = np.asarray(quantiles, float)
    y = np.asarray(y, float)
    if mask is not None:
        m = np.asarray(mask, bool)
        quantiles = quantiles[:, m]
        y = y[m]
    out = np.empty(y.shape[0])
    for i in range(y.shape[0]):
        q = np.sort(quantiles[:, i])
        out[i] = float(np.interp(y[i], q, taus, left=0.0, right=1.0))
    # Randomized tie-break jitter (reproducible via rng).
    out = out + rng.uniform(-1e-9, 1e-9, size=out.shape)
    return np.clip(out, 0.0, 1.0)


def ks_statistic_uniform(samples) -> float:
    """Two-sided KS statistic of samples against Uniform(0,1)."""
    s = np.sort(np.asarray(samples, float))
    n = s.shape[0]
    if n == 0:
        return 0.0
    cdf = np.arange(1, n + 1) / n
    d_plus = np.max(cdf - s)
    d_minus = np.max(s - (np.arange(0, n) / n))
    return float(max(d_plus, d_minus))


# ===========================================================================
# Multivariate scores
# ===========================================================================


def energy_score(y, members, mask=None) -> MetricValue:
    """Energy score, beta=1 (Gneiting & Raftery 2007). y is (d,), members (N, d)
    OR y is (d, n) with members (N, d, n)."""
    members = np.asarray(members, float)
    y = np.asarray(y, float)
    if members.ndim == 2:  # (N, d), single event
        n_mem = members.shape[0]
        term1 = np.mean(np.linalg.norm(members - y[None, :], axis=1))
        diff = np.linalg.norm(members[:, None, :] - members[None, :, :], axis=2)
        term2 = diff.sum() / (2.0 * n_mem * n_mem)
        val = float(term1 - term2)
        return MetricValue(val, 1, "MW", f"members={n_mem}")
    raise ValueError("energy_score expects members (N, d)")


def variogram_score(y, members, weights=None, p=0.5) -> MetricValue:
    """Variogram score of order p (Scheuerer & Hamill 2015). y is (d,), members
    (N, d). Default weights w_ij all 1; pass a (d,d) weight matrix for the
    temporal 1/|i-j| scheme."""
    members = np.asarray(members, float)
    y = np.asarray(y, float)
    d = y.shape[0]
    if weights is None:
        weights = np.ones((d, d))
    total = 0.0
    for i in range(d):
        for j in range(d):
            if i == j:
                continue
            emp = np.abs(y[i] - y[j]) ** p
            mod = np.mean(np.abs(members[:, i] - members[:, j]) ** p)
            total += weights[i, j] * (emp - mod) ** 2
    return MetricValue(float(total), 1, "unitless", f"p={p}")


def temporal_variogram_weights(d: int) -> np.ndarray:
    """w_ij = 1/|i-j| for i != j (temporal, same asset)."""
    w = np.zeros((d, d))
    for i in range(d):
        for j in range(d):
            if i != j:
                w[i, j] = 1.0 / abs(i - j)
    return w


# ===========================================================================
# Diebold-Mariano (design §8.7)
# ===========================================================================


def diebold_mariano(loss_a, loss_b, h=1) -> tuple[float, float]:
    """DM test on loss differentials with Newey-West HAC + HLN(1997) correction.

    Returns (dm_stat, approx_two_sided_pvalue). Serial dependence makes it
    approximate; a difference without a p-value is reported as not assessed.
    """
    from scipy import stats

    d = np.asarray(loss_a, float) - np.asarray(loss_b, float)
    n = d.shape[0]
    dbar = float(np.mean(d))
    # Newey-West long-run variance with Bartlett weights up to lag h-1.
    gamma0 = float(np.mean((d - dbar) ** 2))
    lrv = gamma0
    for k in range(1, h):
        cov = float(np.mean((d[k:] - dbar) * (d[:-k] - dbar)))
        lrv += 2.0 * (1.0 - k / h) * cov
    if lrv <= 0:
        return 0.0, 1.0
    dm = dbar / np.sqrt(lrv / n)
    # HLN small-sample correction factor.
    corr = np.sqrt((n + 1 - 2 * h + h * (h - 1) / n) / n)
    dm *= corr
    pval = float(2.0 * (1.0 - stats.t.cdf(abs(dm), df=n - 1)))
    return float(dm), pval


# ===========================================================================
# §8.8 DSM proxy -- EVALUATION ONLY. Not for production import.
# ===========================================================================


@dataclass(frozen=True)
class DsmChargeResult:
    normalized_index: float
    block_minutes: int
    band_regime: str
    capacity_source: str


class DsmCalculator(Protocol):
    def charge(
        self,
        *,
        scheduled_mw: Sequence[float],
        actual_mw: Sequence[float],
        available_capacity_mw: float,
        technology: Literal["solar", "wind"],
        band_regime: Literal["pre_2026_04_01", "post_2026_04_01"],
        block_minutes: int,
    ) -> DsmChargeResult: ...


_BANDS = {
    ("solar", "pre_2026_04_01"): 0.10,
    ("wind", "pre_2026_04_01"): 0.15,
    ("solar", "post_2026_04_01"): 0.05,
    ("wind", "post_2026_04_01"): 0.10,
}


def dsm_deviation_charge_proxy_evaluation_only(
    *,
    scheduled_mw: Sequence[float],
    actual_mw: Sequence[float],
    available_capacity_mw: float,
    technology: Literal["solar", "wind"],
    band_regime: Literal["pre_2026_04_01", "post_2026_04_01"],
    block_minutes: int,
    capacity_source: str = "scenario_assumption",
) -> DsmChargeResult:
    """Rate-agnostic normalized deviation-exposure index (§8.8).

    Index = sum over blocks of (|deviation| - band)_+ in capacity-fraction units,
    where deviation = (actual - scheduled) / available_capacity_mw. No slab rates
    are fabricated; a priced variant lives elsewhere and requires an explicit rate
    table.
    """
    sched = np.asarray(scheduled_mw, float)
    act = np.asarray(actual_mw, float)
    band = _BANDS[(technology, band_regime)]
    dev = (act - sched) / available_capacity_mw
    beyond = np.maximum(np.abs(dev) - band, 0.0)
    # capacity-fraction-hours beyond band
    index = float(np.sum(beyond) * (block_minutes / 60.0))
    return DsmChargeResult(
        normalized_index=index,
        block_minutes=block_minutes,
        band_regime=band_regime,
        capacity_source=capacity_source,
    )


__all__ = [
    "MetricValue",
    "mae",
    "rmse",
    "nmae",
    "bias",
    "ramp_error",
    "pinball",
    "crps_ensemble",
    "crps_from_quantile_grid",
    "coverage_with_wilson",
    "sharpness",
    "winkler_interval_score",
    "pit_values",
    "ks_statistic_uniform",
    "energy_score",
    "variogram_score",
    "temporal_variogram_weights",
    "diebold_mariano",
    "DsmChargeResult",
    "DsmCalculator",
    "dsm_deviation_charge_proxy_evaluation_only",
]
