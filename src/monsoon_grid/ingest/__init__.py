"""Weather ingest package: offline-first cached fetch over Open-Meteo and NASA POWER."""

from __future__ import annotations

from .base import (
    IngestMode,
    IngestUnit,
    IngestVariable,
    WeatherForecastRecord,
    WeatherObservationRecord,
    WeatherRecord,
    WeatherRequest,
    WeatherSource,
    fetch,
)
from .cache import ResponseCache
from .nasa_power import NasaPowerSource
from .openmeteo import OpenMeteoSource

__all__ = [
    "IngestMode",
    "IngestUnit",
    "IngestVariable",
    "WeatherForecastRecord",
    "WeatherObservationRecord",
    "WeatherRecord",
    "WeatherRequest",
    "WeatherSource",
    "fetch",
    "ResponseCache",
    "NasaPowerSource",
    "OpenMeteoSource",
]
