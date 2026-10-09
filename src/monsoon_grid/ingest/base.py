"""Weather ingest surface: strict request/record contracts and the fetch entrypoint.

Issue-time and valid-time are separated structurally: a ``WeatherForecastRecord``
carries both and requires ``valid_time_utc > issue_time_utc``; a
``WeatherObservationRecord`` carries neither issue time nor lead time. A revised
reanalysis value therefore cannot be typed as a forecast.

Never fabricates: every failure raises; the only data sources are the cache or
the network. ``offline`` is the default mode so the suite and gates need no
network.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import Field, field_validator, model_validator

from ..contracts import Provenance, RecordModel, StrictModel, _id, _utc

if TYPE_CHECKING:
    from .cache import ResponseCache

IngestVariable = Literal[
    "shortwave_radiation",
    "direct_normal_irradiance",
    "temperature_2m",
    "wind_speed_10m",
    "wind_speed_100m",
    "cloud_cover",
]
IngestUnit = Literal["W_per_m2", "degC", "m_per_s", "fraction"]
IngestMode = Literal["offline", "cached_first", "live"]
QualityFlag = Literal["fresh", "stale", "missing", "conflict", "invalid"]


class WeatherRequest(StrictModel):
    site_id: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    start_date: dt.date
    end_date: dt.date
    variables: tuple[IngestVariable, ...]
    issue_date: dt.date | None = None

    _site = field_validator("site_id")(_id)

    @model_validator(mode="after")
    def check_request(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if (self.end_date - self.start_date).days > 31:
            raise ValueError("request window must be at most 31 days")
        if not self.variables:
            raise ValueError("at least one variable is required")
        if list(self.variables) != sorted(set(self.variables)):
            raise ValueError("variables must be sorted and unique")
        return self


class WeatherForecastRecord(RecordModel):
    site_id: str
    source_id: str
    variable: IngestVariable
    issue_time_utc: dt.datetime
    valid_time_utc: dt.datetime
    lead_time_hours: float = Field(ge=0)
    value: float | None
    unit: IngestUnit
    quality: QualityFlag
    source_url: str
    retrieved_at_utc: dt.datetime
    response_content_sha256: str
    provenance: Provenance

    _site = field_validator("site_id")(_id)
    _times = field_validator("issue_time_utc", "valid_time_utc", "retrieved_at_utc")(_utc)

    @model_validator(mode="after")
    def check_forecast(self):
        if not (self.valid_time_utc > self.issue_time_utc):
            raise ValueError("a forecast valid_time must be strictly after its issue_time")
        expected_lead = (self.valid_time_utc - self.issue_time_utc).total_seconds() / 3600.0
        if abs(self.lead_time_hours - expected_lead) > 1e-6:
            raise ValueError("lead_time_hours must equal valid_time - issue_time")
        return self


class WeatherObservationRecord(RecordModel):
    site_id: str
    source_id: str
    variable: IngestVariable
    valid_time_utc: dt.datetime
    value: float | None
    unit: IngestUnit
    quality: QualityFlag
    source_url: str
    retrieved_at_utc: dt.datetime
    response_content_sha256: str
    provenance: Provenance

    _site = field_validator("site_id")(_id)
    _times = field_validator("valid_time_utc", "retrieved_at_utc")(_utc)


WeatherRecord = WeatherForecastRecord | WeatherObservationRecord


class WeatherSource(Protocol):
    source_id: str

    def request_url(self, request: WeatherRequest) -> str: ...

    def parse(self, payload: bytes, request: WeatherRequest, retrieved_at: dt.datetime, content_sha256: str) -> list[WeatherRecord]: ...


def fetch(
    source: WeatherSource,
    request: WeatherRequest,
    *,
    mode: IngestMode = "offline",
    cache: "ResponseCache | None" = None,
) -> list[WeatherRecord]:
    """Fetch records for a request, honouring the cache and mode (at most one network hit).

    ``offline`` (default): cache only; a miss raises ``IngestCacheMissError``.
    ``cached_first``: hit -> cache; miss -> network then cache.
    ``live``: always network then cache.
    """
    import hashlib
    import time

    from ..canonical import sha256_hex
    from ..config import RUNTIME_ROOT
    from ..errors import IngestCacheMissError, IngestFetchError
    from .cache import ResponseCache

    cache = cache or ResponseCache(RUNTIME_ROOT / "cache")
    url = source.request_url(request)
    cache_key = sha256_hex({"source_id": source.source_id, "url": url, "request": request.model_dump(mode="python")})

    if mode in ("offline", "cached_first"):
        entry = cache.get(source.source_id, cache_key)
        if entry is not None:
            payload = entry["body"]
            retrieved_at = dt.datetime.fromisoformat(entry["retrieved_at_utc"].replace("Z", "+00:00"))
            return source.parse(payload, request, retrieved_at, entry["response_content_sha256"])
        if mode == "offline":
            raise IngestCacheMissError(
                f"offline cache miss for {source.source_id} ({url}); populate it with: "
                f"python -m monsoon_grid ingest-weather --source {source.source_id} "
                f"--site-id {request.site_id} --latitude {request.latitude} --longitude {request.longitude} "
                f"--start-date {request.start_date} --end-date {request.end_date} "
                f"{('--issue-date ' + request.issue_date.isoformat()) if request.issue_date else ''} "
                f"--variables {','.join(request.variables)} --mode live",
                code="INGEST_CACHE_MISS",
            )

    # Network path (cached_first miss or live).
    import httpx

    started = time.perf_counter()
    try:
        with httpx.Client(timeout=30.0, follow_redirects=False, headers={"User-Agent": "monsoon-grid-ingest/1.0"}) as client:
            response = client.get(url)
            response.raise_for_status()
            body = response.content
    except Exception as exc:  # noqa: BLE001 - all transport failures surface, no retry
        raise IngestFetchError(
            f"ingest fetch failed for {url}: {type(exc).__name__}: {exc} (elapsed {time.perf_counter() - started:.3f}s, mode={mode})",
            code="INGEST_FETCH_FAILED",
        ) from exc

    content_sha256 = hashlib.sha256(body).hexdigest()
    retrieved_at = dt.datetime.now(dt.timezone.utc)
    cache.put(source.source_id, cache_key, url=url, request=request.model_dump(mode="json"), retrieved_at=retrieved_at, body=body, content_sha256=content_sha256, source_attribution=getattr(source, "source_attribution", source.source_id))
    return source.parse(body, request, retrieved_at, content_sha256)
