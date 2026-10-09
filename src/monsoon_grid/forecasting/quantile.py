"""Part B1 -- residual quantile models (design §5.1, §5.3).

Two estimators over the per-unit residual ``r = (y - B_hat) / C_ac``:
1. Linear quantile regression solved exactly as an LP (HiGHS) -- Koenker & Bassett
   (1978).
2. numpy gradient-boosted quantile trees with the check loss -- Friedman (2001).

Non-crossing is repaired by the rearrangement operator of Chernozhukov,
Fernandez-Val & Galichon (2010). The emitted band order of operations is fixed
and tested: predict -> rearrange -> clip to [0, physical_max_factor*C_ac]; and
conformity scores are computed on the CLIPPED band (§5.1, invariant 4).

No novelty is claimed for any of these established methods.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import linprog

from .errors import QuantileFitError

DEFAULT_TAUS = (0.02, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.98)


# ===========================================================================
# Rearrangement + clip (shared)
# ===========================================================================


def rearrange(quantiles: np.ndarray) -> np.ndarray:
    """Sort each sample's quantile vector so it is non-decreasing in tau.

    ``quantiles`` is (n_levels, n_samples). Returns the sorted copy.
    """
    q = np.asarray(quantiles, dtype=np.float64)
    return np.sort(q, axis=0)


def clip_band(quantiles: np.ndarray, lower: float, upper: float) -> np.ndarray:
    return np.clip(quantiles, lower, upper)


# ===========================================================================
# Linear quantile regression via LP (design §5.3)
# ===========================================================================


def fit_linear_quantile(X: np.ndarray, y: np.ndarray, tau: float, *, l1_ridge: float = 0.0) -> np.ndarray:
    """Exact quantile regression LP (HiGHS). Returns coefficients incl. intercept.

    Standard formulation:
        min sum tau*u_i + (1-tau)*v_i   s.t.  y_i - x_i^T beta = u_i - v_i,  u,v >= 0
    An intercept column is appended. The L1 ridge on non-intercept coefficients
    is expressed by augmentation to keep it an LP.
    """
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    n, p = X.shape
    Xa = np.column_stack([X, np.ones(n)])  # last column intercept
    pa = p + 1

    # Variables: beta+ (pa), beta- (pa), u (n), v (n).
    # beta = beta+ - beta-.
    n_beta = 2 * pa
    n_var = n_beta + 2 * n
    c = np.zeros(n_var)
    # L1 ridge on non-intercept beta (exclude intercept at index p).
    ridge_vec = np.full(pa, l1_ridge)
    ridge_vec[p] = 0.0  # no penalty on intercept
    c[:pa] = ridge_vec
    c[pa:n_beta] = ridge_vec
    c[n_beta : n_beta + n] = tau
    c[n_beta + n :] = 1.0 - tau

    # Equality: Xa @ (beta+ - beta-) + u - v = y.
    A_eq = np.zeros((n, n_var))
    A_eq[:, :pa] = Xa
    A_eq[:, pa:n_beta] = -Xa
    A_eq[:, n_beta : n_beta + n] = np.eye(n)
    A_eq[:, n_beta + n :] = -np.eye(n)
    b_eq = y

    bounds = [(0, None)] * n_var
    res = linprog(c, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if not res.success:
        raise QuantileFitError(f"linprog failed for tau={tau}: {res.message}")
    sol = res.x
    beta = sol[:pa] - sol[pa:n_beta]
    if not np.all(np.isfinite(beta)):
        raise QuantileFitError(f"non-finite coefficients for tau={tau}")
    return beta


def predict_linear(X: np.ndarray, beta: np.ndarray) -> np.ndarray:
    X = np.asarray(X, dtype=np.float64)
    Xa = np.column_stack([X, np.ones(X.shape[0])])
    return Xa @ beta


@dataclass(frozen=True)
class LinearQuantileModel:
    taus: tuple[float, ...]
    coefficients: np.ndarray  # (n_levels, n_features+1)

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Return rearranged (n_levels, n_samples) quantiles."""
        preds = np.stack([predict_linear(X, self.coefficients[i]) for i in range(len(self.taus))], axis=0)
        return rearrange(preds)


def fit_linear_quantile_model(
    X: np.ndarray, y: np.ndarray, taus: tuple[float, ...] = DEFAULT_TAUS, *, l1_ridge: float = 0.0
) -> LinearQuantileModel:
    coeffs = np.stack([fit_linear_quantile(X, y, t, l1_ridge=l1_ridge) for t in taus], axis=0)
    return LinearQuantileModel(taus=tuple(taus), coefficients=coeffs)


# ===========================================================================
# Gradient-boosted quantile trees (design §5.3) -- deterministic numpy
# ===========================================================================


def _weighted_quantile(values: np.ndarray, tau: float) -> float:
    """The tau-weighted quantile (leaf-renewal line-search optimum)."""
    if values.size == 0:
        return 0.0
    return float(np.quantile(values, tau, method="linear"))


@dataclass
class _Node:
    is_leaf: bool
    value: float = 0.0
    feature: int = -1
    bin_threshold: int = -1
    left: "_Node | None" = None
    right: "_Node | None" = None


def _bin_edges(X: np.ndarray, n_bins: int) -> list[np.ndarray]:
    edges = []
    for j in range(X.shape[1]):
        col = X[:, j]
        qs = np.linspace(0, 1, n_bins + 1)
        e = np.unique(np.quantile(col, qs, method="linear"))
        edges.append(e)
    return edges


def _binify(X: np.ndarray, edges: list[np.ndarray]) -> np.ndarray:
    out = np.empty(X.shape, dtype=np.int32)
    for j in range(X.shape[1]):
        out[:, j] = np.clip(np.digitize(X[:, j], edges[j][1:-1]), 0, len(edges[j]) - 1).astype(np.int32)
    return out


def _fit_tree(Xb: np.ndarray, g: np.ndarray, depth: int, max_depth: int, min_leaf: int, n_bins: int) -> _Node:
    n = Xb.shape[0]
    if depth >= max_depth or n < 2 * min_leaf:
        return _Node(is_leaf=True, value=float(np.mean(g)) if n else 0.0)
    best = None  # (sse, feature, bin_threshold)
    total_sum = g.sum()
    for j in range(Xb.shape[1]):
        col = Xb[:, j]
        for b in range(n_bins):
            left_mask = col <= b
            nl = int(left_mask.sum())
            nr = n - nl
            if nl < min_leaf or nr < min_leaf:
                continue
            sl = g[left_mask].sum()
            sr = total_sum - sl
            # Reduction in squared error for a mean-fit split.
            sse = -(sl * sl / nl + sr * sr / nr)
            cand = (sse, j, b)
            if best is None or cand < best:  # deterministic tie-break: lowest (sse, feat, bin)
                best = cand
    if best is None:
        return _Node(is_leaf=True, value=float(np.mean(g)))
    _, feat, thr = best
    left_mask = Xb[:, feat] <= thr
    left = _fit_tree(Xb[left_mask], g[left_mask], depth + 1, max_depth, min_leaf, n_bins)
    right = _fit_tree(Xb[~left_mask], g[~left_mask], depth + 1, max_depth, min_leaf, n_bins)
    return _Node(is_leaf=False, feature=feat, bin_threshold=thr, left=left, right=right)


def _tree_predict(node: _Node, Xb: np.ndarray) -> np.ndarray:
    out = np.empty(Xb.shape[0], dtype=np.float64)
    for i in range(Xb.shape[0]):
        nd = node
        while not nd.is_leaf:
            nd = nd.left if Xb[i, nd.feature] <= nd.bin_threshold else nd.right
        out[i] = nd.value
    return out


@dataclass
class GbmQuantileModel:
    tau: float
    edges: list[np.ndarray]
    init: float
    trees: list[_Node]
    learning_rate: float

    def predict(self, X: np.ndarray) -> np.ndarray:
        Xb = _binify(np.asarray(X, dtype=np.float64), self.edges)
        pred = np.full(Xb.shape[0], self.init)
        for tree in self.trees:
            pred = pred + self.learning_rate * _tree_predict(tree, Xb)
        return pred


def fit_gbm_quantile(
    X: np.ndarray,
    y: np.ndarray,
    tau: float,
    *,
    n_rounds: int = 50,
    learning_rate: float = 0.05,
    max_depth: int = 3,
    min_leaf: int = 50,
    n_bins: int = 32,
) -> GbmQuantileModel:
    """Friedman (2001) gradient boosting with the check loss, deterministic."""
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    edges = _bin_edges(X, n_bins)
    Xb = _binify(X, edges)
    init = _weighted_quantile(y, tau)
    pred = np.full(y.shape[0], init)
    trees: list[_Node] = []
    for _ in range(n_rounds):
        resid = y - pred
        grad = np.where(resid > 0.0, tau, tau - 1.0)  # negative gradient of check loss
        tree = _fit_tree(Xb, grad, 0, max_depth, min_leaf, n_bins)
        # Leaf renewal: replace each leaf value by the tau-weighted quantile of
        # the leaf's residuals.
        _renew_leaves(tree, Xb, resid, tau)
        pred = pred + learning_rate * _tree_predict(tree, Xb)
        trees.append(tree)
    return GbmQuantileModel(tau=tau, edges=edges, init=init, trees=trees, learning_rate=learning_rate)


def _renew_leaves(node: _Node, Xb: np.ndarray, resid: np.ndarray, tau: float) -> None:
    if node.is_leaf:
        node.value = _weighted_quantile(resid, tau) if resid.size else 0.0
        return
    mask = Xb[:, node.feature] <= node.bin_threshold
    _renew_leaves(node.left, Xb[mask], resid[mask], tau)
    _renew_leaves(node.right, Xb[~mask], resid[~mask], tau)


def pinball_loss(y: np.ndarray, pred: np.ndarray, tau: float) -> float:
    e = np.asarray(y, dtype=np.float64) - np.asarray(pred, dtype=np.float64)
    return float(np.mean(np.maximum(tau * e, (tau - 1.0) * e)))


__all__ = [
    "DEFAULT_TAUS",
    "rearrange",
    "clip_band",
    "fit_linear_quantile",
    "predict_linear",
    "LinearQuantileModel",
    "fit_linear_quantile_model",
    "GbmQuantileModel",
    "fit_gbm_quantile",
    "pinball_loss",
]
