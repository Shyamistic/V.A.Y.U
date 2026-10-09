"""MonsoonGrid Phase 2 probabilistic forecasting & calibration subsystem.

A self-contained, read-only package: it does NOT wire itself into the dispatch
engine. A later phase adopts it through :mod:`monsoon_grid.forecasting.interface`
(design §9.6).

Public exports only; no side effects, no heavy imports at package load. In
particular this module must not import ``httpx`` or ``scipy`` at import time, so
``import monsoon_grid.forecasting`` stays cheap and the blast radius of a
concurrent engine edit stays visible (invariant 14).
"""

from __future__ import annotations

from . import errors, types

__all__ = ["errors", "types"]
