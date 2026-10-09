"""D4 regression: offline ingest from the checked-in real sample (no network)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from monsoon_grid.errors import CacheCorruptError, IngestCacheMissError
from monsoon_grid.ingest import (
    NasaPowerSource,
    OpenMeteoSource,
    ResponseCache,
    WeatherForecastRecord,
    WeatherObservationRecord,
    WeatherRequest,
    fetch,
)

SAMPLE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "cache_sample"


def _openmeteo_request():
    return WeatherRequest(
        site_id="bhuj-solar",
        latitude=23.2419,
        longitude=69.6669,
        start_date=dt.date(2024, 7, 1),
        end_date=dt.date(2024, 7, 1),
        variables=("shortwave_radiation", "temperature_2m"),
        issue_date=dt.date(2024, 6, 30),
    )


def _nasa_request():
    return WeatherRequest(
        site_id="bhuj-solar",
        latitude=23.2419,
        longitude=69.6669,
        start_date=dt.date(2024, 7, 1),
        end_date=dt.date(2024, 7, 1),
        variables=("temperature_2m", "wind_speed_10m"),
    )


def test_sample_files_exist():
    # Fail (never skip) if the real sample is missing.
    assert (SAMPLE_ROOT / "openmeteo").is_dir(), SAMPLE_ROOT
    assert (SAMPLE_ROOT / "nasa-power").is_dir(), SAMPLE_ROOT
    assert list((SAMPLE_ROOT / "openmeteo").glob("*.json"))
    assert list((SAMPLE_ROOT / "nasa-power").glob("*.json"))


def test_openmeteo_offline_parse():
    cache = ResponseCache(SAMPLE_ROOT)
    records = fetch(OpenMeteoSource(), _openmeteo_request(), mode="offline", cache=cache)
    assert records
    forecast = records[0]
    assert isinstance(forecast, WeatherForecastRecord)
    assert forecast.issue_time_utc != forecast.valid_time_utc
    assert forecast.valid_time_utc > forecast.issue_time_utc
    expected_lead = (forecast.valid_time_utc - forecast.issue_time_utc).total_seconds() / 3600.0
    assert abs(forecast.lead_time_hours - expected_lead) < 1e-6
    assert forecast.source_url
    assert forecast.retrieved_at_utc
    assert forecast.response_content_sha256
    assert forecast.provenance.label == "derived"


def test_nasa_offline_parse_is_observation_measured():
    cache = ResponseCache(SAMPLE_ROOT)
    records = fetch(NasaPowerSource(), _nasa_request(), mode="offline", cache=cache)
    assert records
    obs = records[0]
    assert isinstance(obs, WeatherObservationRecord)
    assert obs.provenance.label == "measured"
    assert not hasattr(obs, "issue_time_utc")


def test_cache_round_trip_and_corruption_detection(tmp_path):
    cache = ResponseCache(tmp_path)
    cache.put("openmeteo", "k1", url="http://x", request={}, retrieved_at=dt.datetime(2024, 7, 1, tzinfo=dt.timezone.utc), body=b"hello", content_sha256=__import__("hashlib").sha256(b"hello").hexdigest(), source_attribution="x")
    assert cache.get("openmeteo", "k1")["body"] == b"hello"
    # Corrupt the stored body hash.
    path = cache._path("openmeteo", "k1")
    text = path.read_text(encoding="utf-8").replace(__import__("hashlib").sha256(b"hello").hexdigest(), "0" * 64)
    path.write_text(text, encoding="utf-8")
    with pytest.raises(CacheCorruptError):
        cache.get("openmeteo", "k1")


def test_offline_miss_raises_with_cli_command(tmp_path):
    cache = ResponseCache(tmp_path)
    with pytest.raises(IngestCacheMissError) as excinfo:
        fetch(OpenMeteoSource(), _openmeteo_request(), mode="offline", cache=cache)
    assert "ingest-weather" in str(excinfo.value)
