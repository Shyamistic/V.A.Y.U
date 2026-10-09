"""Part B3 -- joint scenario generation (design §7).

Primary: multivariate moving-block bootstrap of PIT-standardized residual vectors
(Kunsch 1989; Politis & Romano 1994). Comparator: Gaussian copula (Sklar 1959;
Pinson et al. 2009) with Spearman->Pearson, separable space-time Kronecker
structure, Higham (2002) nearest-correlation projection. Reduction: Kantorovich
fast-forward selection (Dupacova, Growe-Kuska & Romisch 2003).

No novelty is claimed for any of these established methods.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .errors import DependenceFitError, ScenarioError

# Mirror of the product's config.PSD_TOLERANCE (a NEGATIVE acceptance tolerance,
# not an eigenvalue floor -- NIT 37). Kept as a local constant so library modules
# import only ``canonical`` and ``errors`` from the product (invariant 14). A test
# in test_forecasting_scenarios asserts this stays equal to config.PSD_TOLERANCE.
PSD_TOLERANCE = -1e-10


def spearman_to_pearson(rho_s: np.ndarray) -> np.ndarray:
    """rho_P = 2 sin(pi rho_S / 6) (robust to marginal misspecification)."""
    return 2.0 * np.sin(np.pi * np.asarray(rho_s, float) / 6.0)


def spearman_corr(u: np.ndarray) -> np.ndarray:
    """Spearman correlation of columns of u (already uniform PIT values)."""
    u = np.asarray(u, float)
    ranks = np.apply_along_axis(lambda c: np.argsort(np.argsort(c)), 0, u).astype(float)
    return np.corrcoef(ranks, rowvar=False)


def higham_nearest_correlation(a: np.ndarray, eig_floor: float = 1e-10, max_iter: int = 100) -> np.ndarray:
    """Higham (2002) alternating projections to the nearest correlation matrix.

    The eigenvalue floor is +1e-10, chosen so the result passes the product's
    acceptance check config.PSD_TOLERANCE = -1e-10 (a negative tolerance, not a
    floor -- NIT 37).
    """
    a = np.asarray(a, float)
    y = a.copy()
    ds = np.zeros_like(a)
    for _ in range(max_iter):
        r = y - ds
        # Project onto PSD cone with an eigenvalue floor.
        w, v = np.linalg.eigh((r + r.T) / 2.0)
        w = np.maximum(w, eig_floor)
        x = (v * w) @ v.T
        ds = x - r
        # Project onto unit-diagonal.
        y = x.copy()
        np.fill_diagonal(y, 1.0)
    y = (y + y.T) / 2.0
    np.fill_diagonal(y, 1.0)
    # Verify PSD within the product tolerance.
    min_eig = float(np.min(np.linalg.eigvalsh(y)))
    if min_eig < PSD_TOLERANCE:
        raise DependenceFitError(f"nearest-correlation min eigenvalue {min_eig} < {PSD_TOLERANCE}")
    return y


def kronecker_separable(r_cross: np.ndarray, t_matrix: np.ndarray) -> np.ndarray:
    """Sigma = R_cross (x) T, separable space-time structure (§7.3)."""
    return np.kron(np.asarray(r_cross, float), np.asarray(t_matrix, float))


def temporal_decay_matrix(h: int, tau_hat: float) -> np.ndarray:
    """T[k,l] = exp(-|k-l| / tau_hat)."""
    idx = np.arange(h)
    return np.exp(-np.abs(idx[:, None] - idx[None, :]) / tau_hat)


def separability_departure(sigma_emp: np.ndarray, sigma_kron: np.ndarray) -> float:
    """Frobenius norm of (emp - kron) normalized by ||emp||_F (§7.3)."""
    num = float(np.linalg.norm(sigma_emp - sigma_kron, "fro"))
    den = float(np.linalg.norm(sigma_emp, "fro"))
    return num / den if den > 0 else 0.0


# ===========================================================================
# Block bootstrap (design §7.3)
# ===========================================================================


def moving_block_bootstrap_indices(
    n_rows: int, block_length: int, target_length: int, rng: np.random.Generator
) -> np.ndarray:
    """Contiguous block start indices concatenated to >= target_length (§7.3).

    The SAME block index is applied across all dimensions simultaneously, which
    preserves cross-asset dependence. Returns a flat index array of length
    target_length.
    """
    if block_length <= 0 or block_length > n_rows:
        raise ScenarioError("invalid block length for bootstrap")
    out: list[int] = []
    max_start = n_rows - block_length
    while len(out) < target_length:
        start = int(rng.integers(0, max_start + 1))
        out.extend(range(start, start + block_length))
    return np.asarray(out[:target_length], dtype=np.int64)


# ===========================================================================
# Scenario reduction (design §7.4)
# ===========================================================================


def fast_forward_reduction(
    paths: np.ndarray, k: int
) -> tuple[np.ndarray, np.ndarray]:
    """Kantorovich fast-forward selection (Dupacova et al. 2003).

    ``paths`` is (N_raw, d*H) per-unit vectors. Returns (selected_indices,
    probabilities) where probabilities sum to 1. Distance is plain L2.
    Deterministic: ties broken by lowest path index.
    """
    paths = np.asarray(paths, float)
    n = paths.shape[0]
    if k > n:
        raise ScenarioError(f"K={k} > N_raw={n}")
    if k <= 0:
        raise ScenarioError("K must be >= 1")
    selected: list[int] = []
    # Pairwise distances (vectorized).
    diff = paths[:, None, :] - paths[None, :, :]
    dist = np.linalg.norm(diff, axis=2)

    # First selection: minimize total distance to all others (greedy start).
    totals = dist.sum(axis=1)
    first = int(np.argmin(totals))
    selected.append(first)

    # Incremental nearest-representative distance: start as distance to `first`.
    nearest_dist = dist[:, first].copy()
    is_selected = np.zeros(n, dtype=bool)
    is_selected[first] = True

    while len(selected) < k:
        # Cost of adding candidate c = sum_i min(nearest_dist[i], dist[i, c]).
        # Vectorized over all candidates at once: (n_rows, n) elementwise min.
        cand_cost = np.minimum(nearest_dist[:, None], dist).sum(axis=0)
        cand_cost[is_selected] = np.inf
        best_idx = int(np.argmin(cand_cost))  # ties -> lowest index (argmin)
        selected.append(best_idx)
        is_selected[best_idx] = True
        nearest_dist = np.minimum(nearest_dist, dist[:, best_idx])

    selected_sorted = sorted(selected)
    # Assign each raw path to its nearest representative; probability = mass share.
    sel_arr = np.asarray(selected_sorted)
    nearest = dist[:, sel_arr].argmin(axis=1)
    counts = np.bincount(nearest, minlength=len(sel_arr)).astype(float)
    probs = counts / counts.sum()
    return sel_arr, probs


# ===========================================================================
# Independence-understates-tail demonstration (design §7.5) -- required
# ===========================================================================


@dataclass(frozen=True)
class TailComparison:
    p05_dependent_mw: float
    p05_independent_mw: float
    ratio: float
    es05_dependent_mw: float
    es05_independent_mw: float
    understates: bool


def independence_understates_tail(
    dependent_portfolio: np.ndarray,
    independent_portfolio: np.ndarray,
    *,
    tail_margin_mw: float = 0.5,
) -> TailComparison:
    """Compare aggregate 5th-percentile and ES@5% of dependence-preserving vs
    independent sampling (§7.5).

    Both arrays are (n_samples,) of aggregate portfolio available power (MW).
    Independent sampling narrows the aggregate distribution and understates the
    joint lower tail. Assertion (NIT 39):
        p05_independent >= p05_dependent * 1.02  OR
        (p05_independent - p05_dependent) >= tail_margin_mw
    """
    dep = np.asarray(dependent_portfolio, float)
    ind = np.asarray(independent_portfolio, float)
    p05_dep = float(np.percentile(dep, 5))
    p05_ind = float(np.percentile(ind, 5))
    es_dep = float(np.mean(dep[dep <= p05_dep]))
    es_ind = float(np.mean(ind[ind <= p05_ind]))
    ratio = (p05_ind / p05_dep) if p05_dep != 0 else float("inf")
    understates = (p05_ind >= p05_dep * 1.02) or ((p05_ind - p05_dep) >= tail_margin_mw)
    return TailComparison(
        p05_dependent_mw=p05_dep,
        p05_independent_mw=p05_ind,
        ratio=ratio,
        es05_dependent_mw=es_dep,
        es05_independent_mw=es_ind,
        understates=understates,
    )


def gaussian_copula_sample(
    corr: np.ndarray, n_samples: int, rng: np.random.Generator
) -> np.ndarray:
    """Draw uniforms from a Gaussian copula with the given correlation matrix.

    Returns (n_samples, d) in (0,1) via the standard-normal CDF.
    """
    from scipy.stats import norm

    corr = np.asarray(corr, float)
    d = corr.shape[0]
    chol = np.linalg.cholesky(corr)
    z = rng.standard_normal(size=(n_samples, d))
    x = z @ chol.T
    return norm.cdf(x)


__all__ = [
    "spearman_to_pearson",
    "spearman_corr",
    "higham_nearest_correlation",
    "kronecker_separable",
    "temporal_decay_matrix",
    "separability_departure",
    "moving_block_bootstrap_indices",
    "fast_forward_reduction",
    "TailComparison",
    "independence_understates_tail",
    "gaussian_copula_sample",
]
