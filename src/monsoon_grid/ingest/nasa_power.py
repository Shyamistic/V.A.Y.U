"""NASA POWER hourly point observations.

Public, no API key. Returns WeatherObservationRecord only (provenance
``measured``). It is the physical baseline and has no issue time, which is why
it cannot be used as a forecast.
"""

from __future__ import annotations

import datetime as dt
import json

from ..contracts import Provenance
from .base import IngestUnit, IngestVariable, WeatherObservationRecord, WeatherRecord, WeatherRequest

POWER_HOURLY_URL = "https://power.larc.nasa.gov/api/temporal/hourly/point"

# NASA POWER parameter codes for the subset of ingest variables NASA provides.
_PARAMETER_FOR: dict[IngestVariable, str] = {
    "temperature_2m": "T2M",
    "wind_speed_10m": "WS10M",
    "shortwave_radiation": "ALLSKY_SFC_SW_DWN",
}
_VARIABLE_UNIT: dict[IngestVariable, IngestUnit] = {
    "temperature_2m": "degC",
    "wind_speed_10m": "m_per_s",
    "shortwave_radiation": "W_per_m2",
}


class NasaPowerSource:
    source_id = "nasa-power"
    source_attribution = "These data were obtained from the NASA Langley Research Center POWER Project (https://power.larc.nasa.gov/). Public domain with POWER project citation."

    def request_url(self, request: WeatherRequest) -> str:
        params = ",".join(_PARAMETER_FOR[v] for v in request.variables if v in _PARAMETER_FOR)
        start = request.start_date.strftime("%Y%m%d")
        end = request.end_date.strftime("%Y%m%d")
        return (
            f"{POWER_HOURLY_URL}?parameters={params}&community=RE"
            f"&longitude={request.longitude}&latitude={request.latitude}"
            f"&start={start}&end={end}&format=JSON&time-standard=UTC"
        )

    def parse(self, payload: bytes, request: WeatherRequest, retrieved_at: dt.datetime, content_sha256: str) -> list[WeatherRecord]:
        data = json.loads(payload)
        parameters = data.get("properties", {}).get("parameter", {})
        url = self.request_url(request)
        records: list[WeatherRecord] = []
        for variable in request.variables:
            code = _PARAMETER_FOR.get(variable)
            if code is None or code not in parameters:
                continue
            unit = _VARIABLE_UNIT[variable]
            for stamp, value in sorted(parameters[code].items()):
                # stamp is YYYYMMDDHH in UTC.
                valid_time = dt.datetime(int(stamp[0:4]), int(stamp[4:6]), int(stamp[6:8]), int(stamp[8:10]), tzinfo=dt.timezone.utc)
                missing = value is None or float(value) <= -999.0
                records.append(WeatherObservationRecord(
                    site_id=request.site_id,
                    source_id=self.source_id,
                    variable=variable,
                    valid_time_utc=valid_time,
                    value=None if missing else round(float(value), 6),
                    unit=unit,
                    quality="missing" if missing else "fresh",
                    source_url=url,
                    retrieved_at_utc=retrieved_at,
                    response_content_sha256=content_sha256,
                    provenance=Provenance(label="measured", source_id="nasa-power", source_version="nasa-power-hourly-v1", input_refs=[content_sha256]),
                ))
        return records
