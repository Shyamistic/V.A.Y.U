"""NASA POWER hourly point loader -- truth side only (design §8.4, §8.4.4).

PS reads in kPa and is converted to hPa (x10). SZA is provider-clipped at 90, so
the zenith cross-check (§4.2) is restricted to SZA < 85. fill_value = -999.0,
and any value <= -900 is marked missing (not quarantined).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..errors import SchemaViolation
from .schema import convert_units, mark_missing

FILL = -999.0


@dataclass(frozen=True)
class PowerSeries:
    valid_time_epoch: np.ndarray
    values: dict[str, np.ndarray]
    missing: dict[str, np.ndarray]


def parse_power_payload(payload: dict) -> PowerSeries:
    """Parse the POWER hourly-point JSON into UTC epoch + per-parameter arrays."""
    params = payload["properties"]["parameter"]
    # Keys are "YYYYMMDDHH" strings; build a shared ordered time axis.
    any_key = next(iter(params))
    stamps = sorted(params[any_key].keys())
    epochs = np.array([_power_stamp_to_epoch(s) for s in stamps], dtype=np.int64)
    values: dict[str, np.ndarray] = {}
    missing: dict[str, np.ndarray] = {}
    for name, series in params.items():
        raw = np.array([float(series[s]) for s in stamps], dtype=np.float64)
        var = _POWER_VAR_MAP.get(name, name)
        miss = mark_missing("nasa_power", raw)
        conv = convert_units("nasa_power", var, raw)
        values[var] = conv
        missing[var] = miss
    return PowerSeries(valid_time_epoch=epochs, values=values, missing=missing)


_POWER_VAR_MAP = {
    "ALLSKY_SFC_SW_DWN": "ghi",
    "ALLSKY_SFC_SW_DNI": "dni",
    "ALLSKY_SFC_SW_DIFF": "dhi",
    "T2M": "temperature_2m",
    "WS10M": "wind_speed_10m",
    "WS50M": "wind_speed_50m",
    "PS": "surface_pressure",
    "SZA": "solar_zenith_angle",
}


def _power_stamp_to_epoch(stamp: str) -> int:
    import datetime as dt

    if len(stamp) != 10:
        raise SchemaViolation(f"unexpected POWER timestamp {stamp!r}")
    d = dt.datetime(
        int(stamp[0:4]), int(stamp[4:6]), int(stamp[6:8]), int(stamp[8:10]), tzinfo=dt.timezone.utc
    )
    return int(d.timestamp())


def sza_crosscheck_mask(sza_deg: np.ndarray, max_deg: float = 85.0) -> np.ndarray:
    """Rows admissible for the zenith cross-check (SZA < max_deg)."""
    return np.asarray(sza_deg, dtype=np.float64) < max_deg


__all__ = ["PowerSeries", "parse_power_payload", "sza_crosscheck_mask", "FILL"]
