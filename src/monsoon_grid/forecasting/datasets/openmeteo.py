"""Open-Meteo loaders (design §8.4.1).

Previous Runs is the PRIMARY as-issued source: ``_previous_dayN`` is the value
predicted exactly N*24h before valid time, so ``issue_time = valid_time - N*24h``
is EXACT (no run-cycle rounding). No as-issued lead shorter than 24 h exists on
this endpoint. ``models=`` is pinned explicitly (never ``best_match``).

issue_time and valid_time are kept as separate columns on every record.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..errors import SchemaViolation

SECONDS_PER_DAY = 86400


@dataclass(frozen=True)
class AsIssuedSeries:
    issue_time_epoch: np.ndarray
    valid_time_epoch: np.ndarray
    lead_minutes: np.ndarray
    value: np.ndarray
    variable: str
    as_issued_confidence: str
    model: str


def previous_runs_issue_times(valid_time_epoch: np.ndarray, lead_days: int) -> np.ndarray:
    """issue_time = valid_time - lead_days*24h, EXACT (§8.4.1)."""
    valid = np.asarray(valid_time_epoch, dtype=np.int64)
    issue = valid - lead_days * SECONDS_PER_DAY
    if np.any(issue > valid):
        raise SchemaViolation("issue_time > valid_time in Open-Meteo previous-runs derivation")
    return issue


def parse_previous_runs_payload(
    payload: dict, variable: str, lead_days: int, *, model: str
) -> AsIssuedSeries:
    """Parse one variable's ``_previous_dayN`` series from a Previous Runs JSON.

    ``payload`` is the decoded JSON dict; the hourly block carries ``time`` (ISO)
    and ``<var>_previous_day{N}``.
    """
    hourly = payload["hourly"]
    times = np.array([_iso_to_epoch(t) for t in hourly["time"]], dtype=np.int64)
    key = f"{variable}_previous_day{lead_days}"
    if key not in hourly:
        raise SchemaViolation(f"Open-Meteo payload missing {key}")
    raw = np.array([np.nan if v is None else float(v) for v in hourly[key]], dtype=np.float64)
    issue = previous_runs_issue_times(times, lead_days)
    lead_min = np.full(times.shape[0], lead_days * 1440.0)
    return AsIssuedSeries(
        issue_time_epoch=issue,
        valid_time_epoch=times,
        lead_minutes=lead_min,
        value=raw,
        variable=variable,
        as_issued_confidence="previous_runs_exact_lead",
        model=model,
    )


def _iso_to_epoch(iso: str) -> int:
    import datetime as dt

    # Open-Meteo hourly times are naive local or UTC-labelled; we require the
    # request to pin timezone=UTC so these are UTC.
    s = iso.replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        d = dt.datetime.fromisoformat(s + ":00")
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp())


__all__ = ["AsIssuedSeries", "previous_runs_issue_times", "parse_previous_runs_payload"]
