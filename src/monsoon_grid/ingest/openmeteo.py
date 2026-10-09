"""Open-Meteo Historical Forecast / Previous Runs source.

No API key. ``issue_date`` is REQUIRED: ``issue_time_utc`` is the archived run's
nominal initialization at that date 00:00:00Z, the finest issue-time granularity
the free endpoint exposes. Records are provenance ``derived`` with input_refs
naming the response digest; the raw cached response is retained for re-derivation.
"""

from __future__ import annotations

import datetime as dt
import json

from ..contracts import Provenance
from .base import IngestUnit, IngestVariable, WeatherForecastRecord, WeatherRecord, WeatherRequest

HISTORICAL_FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"

_VARIABLE_UNIT: dict[IngestVariable, IngestUnit] = {
    "shortwave_radiation": "W_per_m2",
    "direct_normal_irradiance": "W_per_m2",
    "temperature_2m": "degC",
    "wind_speed_10m": "m_per_s",
    "wind_speed_100m": "m_per_s",
    "cloud_cover": "fraction",
}


class OpenMeteoSource:
    source_id = "openmeteo"
    source_attribution = "Weather data by Open-Meteo.com, licensed under CC BY 4.0 (https://open-meteo.com/)."

    def __init__(self, *, previous_runs: bool = False) -> None:
        self.base_url = PREVIOUS_RUNS_URL if previous_runs else HISTORICAL_FORECAST_URL

    def request_url(self, request: WeatherRequest) -> str:
        if request.issue_date is None:
            raise ValueError("openmeteo requires an issue_date")
        hourly = ",".join(request.variables)
        return (
            f"{self.base_url}?latitude={request.latitude}&longitude={request.longitude}"
            f"&start_date={request.start_date.isoformat()}&end_date={request.end_date.isoformat()}"
            f"&hourly={hourly}&windspeed_unit=ms&timezone=UTC"
        )

    def parse(self, payload: bytes, request: WeatherRequest, retrieved_at: dt.datetime, content_sha256: str) -> list[WeatherRecord]:
        if request.issue_date is None:
            raise ValueError("openmeteo requires an issue_date")
        data = json.loads(payload)
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        issue_time = dt.datetime(request.issue_date.year, request.issue_date.month, request.issue_date.day, tzinfo=dt.timezone.utc)
        url = self.request_url(request)
        records: list[WeatherRecord] = []
        for variable in request.variables:
            series = hourly.get(variable, [])
            for index, time_text in enumerate(times):
                valid_time = dt.datetime.fromisoformat(time_text).replace(tzinfo=dt.timezone.utc)
                if valid_time <= issue_time:
                    continue  # a value at or before issue time is not a forecast
                value = series[index] if index < len(series) else None
                lead_hours = round((valid_time - issue_time).total_seconds() / 3600.0, 6)
                unit = _VARIABLE_UNIT[variable]
                converted = value
                if variable == "cloud_cover" and value is not None:
                    converted = round(float(value) / 100.0, 6)
                records.append(WeatherForecastRecord(
                    site_id=request.site_id,
                    source_id=self.source_id,
                    variable=variable,
                    issue_time_utc=issue_time,
                    valid_time_utc=valid_time,
                    lead_time_hours=lead_hours,
                    value=None if converted is None else round(float(converted), 6),
                    unit=unit,
                    quality="fresh" if value is not None else "missing",
                    source_url=url,
                    retrieved_at_utc=retrieved_at,
                    response_content_sha256=content_sha256,
                    provenance=Provenance(label="derived", source_id="openmeteo", source_version="open-meteo-historical-forecast-v1", input_refs=[content_sha256]),
                ))
        return records
