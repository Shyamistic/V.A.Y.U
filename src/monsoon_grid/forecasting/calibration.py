"""Part B2 -- distribution-free calibration (design §6).

Base method: CQR (Romano, Patterson & Candes 2019). Two time-series adaptations
implemented and compared head-to-head:
- EnbPI-style ensemble batch prediction intervals (Xu & Xie 2021), adapted to a
  quantile base learner (CQR-form conformity score).
- Adaptive Conformal Inference (Gibbs & Candes 2021), online, per calibration cell.

The §6.5 admissibility rule governs when online state may update: no score with
``valid_time + publication_lag > issue_time`` may enter the state (invariant 2).

No novelty is claimed for any of these established methods.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .errors import EvaluationProtocolError


def cqr_score(y: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """CQR conformity score on a band (computed on the CLIPPED band, §6.2)."""
    y = np.asarray(y, dtype=np.float64)
    lo = np.asarray(lo, dtype=np.float64)
    hi = np.asarray(hi, dtype=np.float64)
    return np.maximum(lo - y, y - hi)


def conformal_quantile(scores: np.ndarray, alpha: float) -> float:
    """The (ceil((n+1)(1-alpha))/n) empirical quantile of conformity scores."""
    s = np.sort(np.asarray(scores, dtype=np.float64))
    n = s.shape[0]
    if n == 0:
        return 0.0
    rank = int(np.ceil((n + 1) * (1.0 - alpha)))
    rank = min(max(rank, 1), n)
    return float(s[rank - 1])


def apply_cqr(
    lo: np.ndarray,
    hi: np.ndarray,
    scores: np.ndarray,
    alpha: float,
    *,
    min_width: float,
    clip_lo: float,
    clip_hi: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Calibrate [lo, hi] by the conformal correction, enforce a width floor,
    then re-clip to [clip_lo, clip_hi] (§6.2)."""
    q = conformal_quantile(scores, alpha)
    new_lo = lo - q
    new_hi = hi + q
    # Width floor (never emit a degenerate zero-width band).
    narrow = (new_hi - new_lo) < min_width
    mid = 0.5 * (new_hi + new_lo)
    new_lo = np.where(narrow, mid - min_width / 2.0, new_lo)
    new_hi = np.where(narrow, mid + min_width / 2.0, new_hi)
    new_lo = np.clip(new_lo, clip_lo, clip_hi)
    new_hi = np.clip(new_hi, clip_lo, clip_hi)
    return new_lo, new_hi


# ===========================================================================
# §6.5 admissibility
# ===========================================================================


def admissible_mask(
    score_valid_times: np.ndarray,
    issue_time: int,
    publication_lag_seconds: int,
) -> np.ndarray:
    """S(t,c): scores whose valid_time + publication_lag <= issue_time (§6.5)."""
    vt = np.asarray(score_valid_times, dtype=np.int64)
    return (vt + publication_lag_seconds) <= issue_time


def assert_admissible(score_valid_times: np.ndarray, issue_time: int, publication_lag_seconds: int) -> None:
    """Raise if any score is inadmissible (a leak into calibration state)."""
    vt = np.asarray(score_valid_times, dtype=np.int64)
    if np.any((vt + publication_lag_seconds) > issue_time):
        raise EvaluationProtocolError("inadmissible score (valid_time + lag > issue_time) reached calibration state")


# ===========================================================================
# ACI (design §6.4)
# ===========================================================================


def aci_clip_bounds(alpha_nominal: float) -> tuple[float, float]:
    """Per-nominal-level relative clip bounds (two-sided at every level, §6.4)."""
    lo = max(1e-3, alpha_nominal / 4.0)
    hi = min(0.9, 3.0 * alpha_nominal)
    return lo, hi


@dataclass
class ACIState:
    alpha_nominal: float
    gamma: float
    alpha_t: float
    clip_lo: float
    clip_hi: float
    errors: list[int] = field(default_factory=list)
    clip_hits: int = 0

    def update(self, covered: bool) -> float:
        """One ACI step. ``covered`` True iff y was inside C_t(alpha_t). Returns the
        next effective alpha."""
        err = 0 if covered else 1
        self.errors.append(err)
        self.alpha_t = self.alpha_t + self.gamma * (self.alpha_nominal - err)
        clipped = min(max(self.alpha_t, self.clip_lo), self.clip_hi)
        if clipped != self.alpha_t:
            self.clip_hits += 1
        self.alpha_t = clipped
        return self.alpha_t

    @property
    def realized_miscoverage(self) -> float:
        return float(np.mean(self.errors)) if self.errors else 0.0

    @property
    def clip_fraction(self) -> float:
        return (self.clip_hits / len(self.errors)) if self.errors else 0.0


def make_aci_state(alpha_nominal: float, gamma: float = 0.01) -> ACIState:
    lo, hi = aci_clip_bounds(alpha_nominal)
    return ACIState(alpha_nominal=alpha_nominal, gamma=gamma, alpha_t=alpha_nominal, clip_lo=lo, clip_hi=hi)


def aci_coverage_bound(alpha_nominal: float, gamma: float, n_steps: int) -> float:
    """Gibbs-Candes (2021) marginal coverage bound (design §6.4).

        | (1/T) Sum_t err_t - alpha_nominal |  <=  ( max(a1, 1 - a1) + gamma ) / (T * gamma)

    where the Gibbs-Candes numerator ``max(a1, 1 - a1)`` is taken OVER THE CLIP
    RANGE ``a1 in [alpha_clip_lo, alpha_clip_hi]`` -- it is the worst case the
    clipped effective level ``alpha_t`` can reach, not the single value at
    ``alpha_nominal``. Because ``a -> max(a, 1 - a)`` is convex and decreasing
    then increasing with its minimum at a=0.5, its maximum over the interval is
    attained at an endpoint:

        max_{a in [lo, hi]} max(a, 1 - a) = max(hi, 1 - lo)

    so the printed numerator is ``max(alpha_clip_hi, 1 - alpha_clip_lo) + gamma``.
    This is the §6.4 form after MEDIUM-11: evaluating the numerator at
    ``alpha_nominal`` alone would understate the bound by roughly a factor of two
    because ``alpha_t`` can be clipped well away from ``alpha_nominal``. Returns
    the bound on the long-run marginal miscoverage gap over ``n_steps`` updates.
    """
    lo, hi = aci_clip_bounds(alpha_nominal)
    numerator = max(hi, 1.0 - lo) + gamma
    if n_steps <= 0:
        return float("inf")
    return numerator / (n_steps * gamma)


# ===========================================================================
# EnbPI LOO bookkeeping (design §6.3)
# ===========================================================================


def enbpi_loo_aggregate(
    member_predictions: np.ndarray,
    member_saw_timestamp: np.ndarray,
    phi: str = "median",
) -> np.ndarray:
    """Leave-one-out aggregate over members that did NOT see each timestamp (§6.3).

    Parameters
    ----------
    member_predictions:
        (B, n) predictions per bootstrap member.
    member_saw_timestamp:
        (B, n) bool; True iff member b's bootstrap set contained timestamp i.
    """
    preds = np.asarray(member_predictions, dtype=np.float64)
    saw = np.asarray(member_saw_timestamp, dtype=bool)
    n = preds.shape[1]
    out = np.full(n, np.nan)
    for i in range(n):
        not_saw = ~saw[:, i]
        if not np.any(not_saw):
            # Fall back to all members if every member saw it (degenerate).
            vals = preds[:, i]
        else:
            vals = preds[not_saw, i]
        out[i] = np.median(vals) if phi == "median" else np.mean(vals)
    return out


def empirical_coverage(y: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> float:
    y = np.asarray(y, dtype=np.float64)
    return float(np.mean((lo <= y) & (y <= hi)))


__all__ = [
    "cqr_score",
    "conformal_quantile",
    "apply_cqr",
    "admissible_mask",
    "assert_admissible",
    "aci_clip_bounds",
    "ACIState",
    "make_aci_state",
    "aci_coverage_bound",
    "enbpi_loo_aggregate",
    "empirical_coverage",
]
