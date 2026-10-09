"""Leakage-safe feature builder -- the single choke point (design §5.2, invariant 1).

``build_features`` slices observations to ``timestamp <= issue_time -
publication_lag`` ONCE at the top, before any feature code runs; downstream
helpers see only the truncated view. A missing lag is an explicit
``*_is_missing`` indicator plus a neutral fill (never forward-filled from the
future). ``curtailment_status`` is never a feature.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .axis import RegularAxis, lag_value


@dataclass(frozen=True)
class FeatureMatrix:
    """Dense feature matrix plus the ordered feature names."""

    names: list[str]
    matrix: np.ndarray  # (n_rows, n_features)

    def column(self, name: str) -> np.ndarray:
        return self.matrix[:, self.names.index(name)]


def _calendar_features(valid_epoch: np.ndarray) -> dict[str, np.ndarray]:
    secs_of_day = np.mod(valid_epoch, 86400.0)
    tod = secs_of_day / 86400.0
    doy = np.mod(valid_epoch, 365.25 * 86400.0) / 86400.0 / 365.25
    month = ((np.mod(valid_epoch, 365.25 * 86400.0) / 86400.0) // 30.4).astype(int) + 1
    return {
        "tod_sin1": np.sin(2 * np.pi * tod),
        "tod_cos1": np.cos(2 * np.pi * tod),
        "tod_sin2": np.sin(4 * np.pi * tod),
        "tod_cos2": np.cos(4 * np.pi * tod),
        "doy_sin": np.sin(2 * np.pi * doy),
        "doy_cos": np.cos(2 * np.pi * doy),
        "is_monsoon": np.isin(np.clip(month, 1, 12), [6, 7, 8, 9]).astype(np.float64),
    }


def build_features(
    issue_time_epoch: int,
    valid_times_epoch: np.ndarray,
    observations: RegularAxis,
    *,
    nwp_forecast: dict[str, np.ndarray] | None = None,
    publication_lag_steps: int = 1,
    lag_set: tuple[int, ...] = (1, 2, 3, 4, 8),
    day_lag_steps: int = 96,
    neutral_fills: dict[str, float] | None = None,
    native_step_minutes: int = 15,
) -> FeatureMatrix:
    """Build the leakage-safe feature matrix for a set of valid times.

    Parameters
    ----------
    issue_time_epoch:
        The decision time. No feature may read an observation newer than
        ``issue_time - publication_lag``.
    valid_times_epoch:
        The forecast valid times (one row per valid time).
    observations:
        The regular axis of observed per-unit power (and whatever else).
    nwp_forecast:
        As-issued exogenous NWP series keyed by name, aligned to valid times.
    """
    valid = np.asarray(valid_times_epoch, dtype=np.float64)
    n = valid.shape[0]
    neutral_fills = neutral_fills or {}

    # SINGLE truncation point: the newest admissible observation index.
    cutoff_epoch = issue_time_epoch - publication_lag_steps * observations.step_seconds
    admissible = observations.present_mask & (observations.axis <= cutoff_epoch)
    # The most recent admissible index (or -1 if none).
    adm_indices = np.flatnonzero(admissible)
    last_adm = int(adm_indices[-1]) if adm_indices.size else -1

    cols: dict[str, np.ndarray] = {}

    # Horizon features.
    lead_minutes = (valid - issue_time_epoch) / 60.0
    cols["lead_minutes"] = lead_minutes
    cols["lead_steps"] = lead_minutes / native_step_minutes
    cols["log1p_lead"] = np.log1p(np.maximum(lead_minutes, 0.0))

    # Calendar (valid time).
    cols.update(_calendar_features(valid))

    # Issue-time calendar (NWP run-dependent bias).
    issue_tod = (issue_time_epoch % 86400) / 86400.0
    cols["issue_tod_sin"] = np.full(n, np.sin(2 * np.pi * issue_tod))
    cols["issue_tod_cos"] = np.full(n, np.cos(2 * np.pi * issue_tod))

    # Recent lags (computed from the truncated view only).
    for lag in lag_set:
        val, missing = (np.nan, True)
        if last_adm >= 0:
            val, missing = lag_value(observations, last_adm, lag - 1)
        fill = neutral_fills.get(f"lag_{lag}", 0.0)
        cols[f"lag_{lag}"] = np.full(n, val if not missing else fill)
        cols[f"lag_{lag}_is_missing"] = np.full(n, 1.0 if missing else 0.0)
    # 24h-ago lag.
    val, missing = (np.nan, True)
    if last_adm >= 0:
        val, missing = lag_value(observations, last_adm, day_lag_steps)
    fill = neutral_fills.get("lag_day", 0.0)
    cols["lag_day"] = np.full(n, val if not missing else fill)
    cols["lag_day_is_missing"] = np.full(n, 1.0 if missing else 0.0)

    # Recent volatility over the admissible tail (strictly from the truncated view).
    if adm_indices.size >= 2:
        recent = observations.values[adm_indices[-min(8, adm_indices.size):]]
        recent = recent[np.isfinite(recent)]
        vol = float(np.std(recent)) if recent.size >= 2 else 0.0
    else:
        vol = 0.0
    cols["recent_volatility"] = np.full(n, vol)

    # Exogenous NWP (as issued), with per-series _is_missing companions.
    if nwp_forecast:
        for name, series in sorted(nwp_forecast.items()):
            arr = np.asarray(series, dtype=np.float64)
            if arr.shape[0] != n:
                raise ValueError(f"nwp series {name!r} length {arr.shape[0]} != {n}")
            miss = ~np.isfinite(arr)
            filled = np.where(miss, neutral_fills.get(name, 0.0), arr)
            cols[name] = filled
            cols[f"{name}_is_missing"] = miss.astype(np.float64)

    names = list(cols.keys())
    matrix = np.column_stack([cols[k] for k in names]) if names else np.zeros((n, 0))
    return FeatureMatrix(names=names, matrix=matrix.astype(np.float64))


__all__ = ["FeatureMatrix", "build_features"]
