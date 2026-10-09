"""EMSx loader (design §8.4.3).

Zenodo record 5510400, ODC-ODbL (not redistributed). Per-site files are gzip,
';'-delimited, 197 columns:
    timestamp;site_id;period_id;actual_consumption;actual_pv;load_00..load_95;pv_00..pv_95
Melt rule: ``pv_k`` -> (issue_time = timestamp, valid_time = timestamp + 15k min).
Two-pass streaming into preallocated numpy arrays (§3.1); a count mismatch is a
DatasetParseError, never a silent resize. kW -> MW once at ingestion.
``capacity_source = "derived"`` from max(actual_pv) over the TRAINING block only.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..errors import DatasetParseError, SchemaViolation

EXPECTED_PREFIX = ["timestamp", "site_id", "period_id", "actual_consumption", "actual_pv"]
N_TRAJECTORY = 96  # load_00..95 and pv_00..95
SECONDS_PER_STEP = 15 * 60


@dataclass(frozen=True)
class EmsxMelted:
    issue_time_epoch: np.ndarray
    valid_time_epoch: np.ndarray
    pv_forecast_mw: np.ndarray
    actual_pv_mw: np.ndarray
    period_id: np.ndarray


def validate_header(header: list[str]) -> None:
    """Validate the 197-column EMSx header exactly (§8.4.3)."""
    if header[:5] != EXPECTED_PREFIX:
        raise SchemaViolation(f"unexpected EMSx header prefix: {header[:5]}")
    expected_len = 5 + 2 * N_TRAJECTORY
    if len(header) != expected_len:
        raise SchemaViolation(f"EMSx header has {len(header)} cols, expected {expected_len}")
    for k in range(N_TRAJECTORY):
        if header[5 + k] != f"load_{k:02d}":
            raise SchemaViolation(f"EMSx column {5 + k} is {header[5 + k]}, expected load_{k:02d}")
        if header[5 + N_TRAJECTORY + k] != f"pv_{k:02d}":
            raise SchemaViolation(f"EMSx pv column is {header[5 + N_TRAJECTORY + k]}, expected pv_{k:02d}")


def melt_rows(rows: list[list[str]]) -> EmsxMelted:
    """Two-pass melt of pv_* trajectories into (issue, valid) records (§8.4.3).

    kW -> MW at ingestion; small negative night-time pv clamped to 0 with a count
    is handled downstream by schema validation. Returns a melted record set.
    """
    expected_fields = 5 + 2 * N_TRAJECTORY
    # Pass 1: a cheap counting/validation pass over the rows BEFORE allocation.
    # Every row's field count is checked here so a malformed row is rejected up
    # front and the pass-2 ``cursor != total`` guard is a genuine two-pass
    # invariant rather than a check that can only trip mid-fill (review F3).
    n_rows = 0
    for row in rows:
        if len(row) != expected_fields:
            raise DatasetParseError(f"EMSx row has {len(row)} fields, expected {expected_fields}")
        n_rows += 1
    total = n_rows * N_TRAJECTORY
    issue = np.empty(total, dtype=np.int64)
    valid = np.empty(total, dtype=np.int64)
    pv = np.empty(total, dtype=np.float64)
    actual = np.empty(total, dtype=np.float64)
    period = np.empty(total, dtype=np.int64)

    # Pass 2: fill.
    cursor = 0
    for row in rows:
        if len(row) != 5 + 2 * N_TRAJECTORY:
            raise DatasetParseError(f"EMSx row has {len(row)} fields, expected {5 + 2 * N_TRAJECTORY}")
        ts = _iso_to_epoch(row[0])
        pid = int(float(row[2]))
        act_pv = float(row[4]) / 1000.0  # kW -> MW
        for k in range(N_TRAJECTORY):
            issue[cursor] = ts
            valid[cursor] = ts + k * SECONDS_PER_STEP
            pv[cursor] = float(row[5 + N_TRAJECTORY + k]) / 1000.0  # kW -> MW
            actual[cursor] = act_pv
            period[cursor] = pid
            cursor += 1

    if cursor != total:
        raise DatasetParseError(f"two-pass count mismatch: filled {cursor}, sized {total}")

    return EmsxMelted(
        issue_time_epoch=issue,
        valid_time_epoch=valid,
        pv_forecast_mw=pv,
        actual_pv_mw=actual,
        period_id=period,
    )


def derived_capacity_mw(actual_pv_mw: np.ndarray, training_mask: np.ndarray) -> float:
    """capacity_source = 'derived' = max(actual_pv) over the TRAINING block only."""
    vals = np.asarray(actual_pv_mw, dtype=np.float64)[np.asarray(training_mask, dtype=bool)]
    if vals.size == 0:
        raise DatasetParseError("cannot derive capacity from an empty training block")
    return float(np.max(vals))


def _iso_to_epoch(iso: str) -> int:
    import datetime as dt

    d = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp())


__all__ = [
    "EmsxMelted",
    "validate_header",
    "melt_rows",
    "derived_capacity_mw",
    "N_TRAJECTORY",
    "EXPECTED_PREFIX",
]
