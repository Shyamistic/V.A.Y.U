"""Self-generated Tier C data for machinery/determinism runs (design §8.2 Tier C).

Tier C licenses ONLY metric-implementation correctness, the Jensen-gap and
dependence demonstrations, determinism, and interface shape. It licenses NO
headline skill or coverage number. Everything generated here is labelled
``synthetic`` and must never be presented as a validated result.
"""

from __future__ import annotations

import numpy as np

STEP_15 = 900


def synthetic_pv_series(n_days: int, rng: np.random.Generator, capacity_mw: float = 100.0):
    """A synthetic diurnal PV availability series on a 15-min axis (Tier C only)."""
    steps = n_days * 96
    t = np.arange(steps)
    tod = (t % 96) / 96.0
    # Diurnal bell + cloud noise, clipped to [0, capacity].
    diurnal = np.clip(np.sin(np.pi * np.clip((tod - 0.25) / 0.5, 0, 1)), 0, 1)
    cloud = np.clip(1.0 - 0.4 * np.abs(rng.normal(0, 1, size=steps)), 0, 1)
    y = diurnal * cloud * capacity_mw
    epoch = t.astype(np.int64) * STEP_15
    return epoch, y


def synthetic_as_issued_forecast(y_true: np.ndarray, rng: np.random.Generator):
    """A biased, noisy 'forecast' of the truth (synthetic as-issued proxy)."""
    bias = 0.05 * y_true.mean()
    noise = rng.normal(0, 0.1 * (y_true.std() + 1e-9), size=y_true.shape)
    return np.clip(y_true + bias + noise, 0, None)


__all__ = ["synthetic_pv_series", "synthetic_as_issued_forecast", "STEP_15"]
