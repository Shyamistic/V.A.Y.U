"""Canonical dataset schema + per-provider unit/sentinel map + §12 validation.

Units are converted exactly once, here, at ingestion (invariant 9). The §8.4.4
provider map and the §12 range rules live together so the invariant is
enforceable. "Quarantine" excludes a row (counted by reason); "missing" is a
separate, separately-thresholded category; neither is silently imputed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..errors import DatasetQualityError, SchemaViolation

# --------------------------------------------------------------------------- unit conversions


def convert_units(provider: str, variable: str, value: np.ndarray) -> np.ndarray:
    """Apply the §8.4.4 unit conversion for (provider, variable). Called ONCE."""
    v = np.asarray(value, dtype=np.float64)
    if provider == "nasa_power" and variable == "surface_pressure":
        return v * 10.0  # kPa -> hPa
    if provider == "openmeteo" and variable == "wind_speed":
        return v / 3.6  # km/h -> m/s
    if provider == "gefcom":
        if variable == "ghi":  # SSRD already de-accumulated upstream (J/m2 -> W/m2 there)
            return v
        if variable == "temperature_2m":
            return v - 273.15  # K -> degC
        if variable == "surface_pressure":
            return v / 100.0  # Pa -> hPa
    if provider == "emsx":
        return v / 1000.0  # kW -> MW
    return v


def mark_missing(provider: str, value: np.ndarray) -> np.ndarray:
    """Return a bool mask of sentinel/null rows (counted as missing, not invalid)."""
    v = np.asarray(value, dtype=np.float64)
    miss = ~np.isfinite(v)
    if provider == "nasa_power":
        miss = miss | (v <= -900.0)  # fill_value = -999.0
    return miss


# --------------------------------------------------------------------------- §12 validation

_RANGES = {
    "ghi": (0.0, 1400.0),
    "dni": (0.0, 1400.0),
    "dhi": (0.0, 1400.0),
    "temperature_2m": (-40.0, 60.0),
    "wind_speed": (0.0, 60.0),
    "relative_humidity": (0.0, 100.0),
    "surface_pressure": (500.0, 1100.0),
}

_CLAMP_WITH_COUNT = {"relative_humidity"}


@dataclass
class QuarantineReport:
    quarantined: dict[str, int] = field(default_factory=dict)
    missing: dict[str, int] = field(default_factory=dict)
    clamped: dict[str, int] = field(default_factory=dict)
    n_total: int = 0

    def missing_fraction(self, variable: str) -> float:
        return (self.missing.get(variable, 0) / self.n_total) if self.n_total else 0.0

    def quarantine_fraction(self, variable: str) -> float:
        return (self.quarantined.get(variable, 0) / self.n_total) if self.n_total else 0.0


def validate_variable(
    variable: str,
    value: np.ndarray,
    missing_mask: np.ndarray,
    report: QuarantineReport,
    *,
    max_quarantine_fraction: float = 0.05,
    max_missing_fraction: float = 0.20,
) -> tuple[np.ndarray, np.ndarray]:
    """Validate a variable's values against §12 ranges.

    Returns (clean_values, usable_mask) where usable rows are neither missing nor
    quarantined. Raises :class:`DatasetQualityError` if either threshold breaches.
    """
    v = np.asarray(value, dtype=np.float64).copy()
    n = v.shape[0]
    report.n_total = max(report.n_total, n)
    missing = np.asarray(missing_mask, dtype=bool)
    report.missing[variable] = int(np.sum(missing))

    quar = np.zeros(n, dtype=bool)
    if variable in _RANGES:
        lo, hi = _RANGES[variable]
        if variable in _CLAMP_WITH_COUNT:
            out_of_range = (~missing) & ((v < lo) | (v > hi))
            report.clamped[variable] = report.clamped.get(variable, 0) + int(np.sum(out_of_range))
            v = np.clip(v, lo, hi)
        else:
            # Small negative irradiance clamp (> -5 W/m2) to 0.
            if variable in {"ghi", "dni", "dhi"}:
                small_neg = (~missing) & (v < 0.0) & (v > -5.0)
                report.clamped[variable] = report.clamped.get(variable, 0) + int(np.sum(small_neg))
                v = np.where(small_neg, 0.0, v)
            quar = (~missing) & ((v < lo) | (v > hi))
            report.quarantined[variable] = int(np.sum(quar))

    if report.quarantine_fraction(variable) > max_quarantine_fraction:
        raise DatasetQualityError(
            f"{variable} quarantine fraction {report.quarantine_fraction(variable):.3f} "
            f"exceeds {max_quarantine_fraction}"
        )
    if report.missing_fraction(variable) > max_missing_fraction:
        raise DatasetQualityError(
            f"{variable} missing fraction {report.missing_fraction(variable):.3f} "
            f"exceeds {max_missing_fraction}"
        )

    usable = (~missing) & (~quar)
    return v, usable


def validate_total_cloud_cover(value: np.ndarray) -> np.ndarray:
    """TCC must be a fraction in [0,1]; a value > 1 is a unit-assumption violation
    (SchemaViolation), not a clamp (§12)."""
    v = np.asarray(value, dtype=np.float64)
    finite = np.isfinite(v)
    if np.any(finite & (v > 1.0)):
        raise SchemaViolation("total_cloud_cover > 1 (unit-assumption violation)")
    return v


def check_issue_before_valid(issue_epoch: np.ndarray, valid_epoch: np.ndarray) -> None:
    issue = np.asarray(issue_epoch, dtype=np.int64)
    valid = np.asarray(valid_epoch, dtype=np.int64)
    if np.any(issue > valid):
        raise SchemaViolation("issue_time > valid_time")


def check_no_duplicate_keys(site_ids, variables, issue_epoch, valid_epoch) -> None:
    keys = list(zip(site_ids, variables, np.asarray(issue_epoch).tolist(), np.asarray(valid_epoch).tolist()))
    if len(keys) != len(set(keys)):
        raise SchemaViolation("duplicate (site, variable, issue_time, valid_time) key")


def deaccumulate_ssrd(ssrd_j_m2: np.ndarray) -> tuple[np.ndarray, int, np.ndarray]:
    """GEFCom SSRD J/m^2 accumulated -> W/m^2 hourly mean (§8.4.2).

    Negative differences are run-boundary resets: use SSRD[t]/3600 and count.
    Returns (ghi_w_m2, reset_count, is_boundary_mask).
    """
    s = np.asarray(ssrd_j_m2, dtype=np.float64)
    n = s.shape[0]
    ghi = np.empty(n, dtype=np.float64)
    boundary = np.zeros(n, dtype=bool)
    ghi[0] = s[0] / 3600.0
    boundary[0] = True
    reset_count = 0
    for t in range(1, n):
        diff = s[t] - s[t - 1]
        if diff < 0.0:
            ghi[t] = s[t] / 3600.0
            boundary[t] = True
            reset_count += 1
        else:
            ghi[t] = diff / 3600.0
    return ghi, reset_count, boundary


__all__ = [
    "convert_units",
    "mark_missing",
    "QuarantineReport",
    "validate_variable",
    "validate_total_cloud_cover",
    "check_issue_before_valid",
    "check_no_duplicate_keys",
    "deaccumulate_ssrd",
]
