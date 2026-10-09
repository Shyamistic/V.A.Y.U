"""Tests for dataset schema, unit/sentinel map, §12 validation, cache (design §8.4-8.6, §12)."""

from __future__ import annotations

import json

import numpy as np
import pytest

from monsoon_grid.forecasting.datasets import cache as C
from monsoon_grid.forecasting.datasets import schema as SC
from monsoon_grid.forecasting.errors import (
    CacheIntegrityError,
    DatasetQualityError,
    DatasetUnavailable,
    SchemaViolation,
)

# --------------------------------------------------------------------------- unit conversions


def test_unit_conversions_exact():
    np.testing.assert_allclose(SC.convert_units("nasa_power", "surface_pressure", np.array([98.0])), [980.0])
    np.testing.assert_allclose(SC.convert_units("openmeteo", "wind_speed", np.array([36.0])), [10.0])
    np.testing.assert_allclose(SC.convert_units("gefcom", "temperature_2m", np.array([300.0])), [26.85])
    np.testing.assert_allclose(SC.convert_units("gefcom", "surface_pressure", np.array([101325.0])), [1013.25])
    np.testing.assert_allclose(SC.convert_units("emsx", "actual_pv", np.array([1500.0])), [1.5])


def test_sentinel_marked_missing_not_quarantine():
    v = np.array([30.0, -999.0, 25.0])  # valid temps + one sentinel
    miss = SC.mark_missing("nasa_power", v)
    assert miss.tolist() == [False, True, False]
    report = SC.QuarantineReport()
    _, usable = SC.validate_variable("temperature_2m", v, miss, report, max_missing_fraction=0.5)
    # The -999 is counted as missing, not quarantined.
    assert report.missing["temperature_2m"] == 1
    assert report.quarantined.get("temperature_2m", 0) == 0
    assert usable.tolist() == [True, False, True]


def test_range_quarantine_with_reason_and_threshold():
    # 10% of pressure rows out of range -> breaches 5% quarantine threshold.
    v = np.array([1000.0] * 90 + [2000.0] * 10)
    miss = np.zeros(100, dtype=bool)
    report = SC.QuarantineReport()
    with pytest.raises(DatasetQualityError):
        SC.validate_variable("surface_pressure", v, miss, report)
    assert report.quarantined["surface_pressure"] == 10


def test_small_negative_irradiance_clamped():
    v = np.array([-3.0, 500.0, -0.5])
    miss = np.zeros(3, dtype=bool)
    report = SC.QuarantineReport()
    clean, usable = SC.validate_variable("ghi", v, miss, report)
    assert clean[0] == 0.0 and clean[2] == 0.0
    assert report.clamped["ghi"] == 2
    assert usable.all()


def test_tcc_above_one_is_schema_violation():
    with pytest.raises(SchemaViolation):
        SC.validate_total_cloud_cover(np.array([0.5, 1.2]))
    # within [0,1] ok
    SC.validate_total_cloud_cover(np.array([0.0, 1.0]))


def test_issue_before_valid_and_duplicate_keys():
    with pytest.raises(SchemaViolation):
        SC.check_issue_before_valid(np.array([200]), np.array([100]))
    with pytest.raises(SchemaViolation):
        SC.check_no_duplicate_keys(["s", "s"], ["pv", "pv"], [0, 0], [900, 900])
    SC.check_no_duplicate_keys(["s", "s"], ["pv", "pv"], [0, 900], [900, 1800])


def test_ssrd_deaccumulation_with_negative_boundary():
    # Accumulated J/m2 within a run resets at index 3.
    ssrd = np.array([0.0, 3600.0, 10800.0, 1800.0], dtype=np.float64)
    ghi, resets, boundary = SC.deaccumulate_ssrd(ssrd)
    # diffs: [first=0/3600=0], (3600-0)/3600=1, (10800-3600)/3600=2, negative->1800/3600=0.5
    np.testing.assert_allclose(ghi, [0.0, 1.0, 2.0, 0.5])
    assert resets == 1
    assert boundary.tolist() == [True, False, False, True]


# --------------------------------------------------------------------------- cache


def test_cache_offline_miss_raises(tmp_path, monkeypatch):
    monkeypatch.setenv(C.OFFLINE_ENV, "1")
    cache = C.ContentAddressedCache(tmp_path)
    with pytest.raises(DatasetUnavailable):
        cache.fetch("https://example.com/data")


def test_cache_digest_tamper_raises(tmp_path):
    cache = C.ContentAddressedCache(tmp_path)
    key = C.request_key("https://example.com/x", None)
    d = tmp_path / key
    d.mkdir(parents=True)
    (d / "payload").write_bytes(b"hello")
    meta = {"payload_sha256": C._sha256_bytes(b"hello"), "url": "https://example.com/x"}
    (d / "meta.json").write_text(json.dumps(meta))
    entry = cache.get(key)
    assert entry is not None
    assert entry.read_bytes() == b"hello"
    # Tamper the payload.
    (d / "payload").write_bytes(b"tampered")
    with pytest.raises(CacheIntegrityError):
        cache.get(key).read_bytes()


def _seed_cache_entry(cache, url, body: bytes, *, content_type=None):
    """Write a cache entry directly (no network) so fetch() reads it as a hit."""
    key = C.request_key(url, None)
    d = cache.root / key
    d.mkdir(parents=True)
    (d / "payload").write_bytes(body)
    meta = {
        "url": url,
        "params": {},
        "final_url": url,
        "redirect_chain": [],
        "declared_length": len(body),
        "observed_length": len(body),
        "content_type": content_type,
        "payload_sha256": C._sha256_bytes(body),
        "http_status": 200,
    }
    (d / "meta.json").write_text(json.dumps(meta))
    return key


def test_cache_rejects_zero_length_body(tmp_path):
    """A 0-byte 200 body (empty-bytes SHA256) is not an obtained dataset."""
    cache = C.ContentAddressedCache(tmp_path)
    url = "https://previous-runs-api.open-meteo.com/v1/forecast"
    _seed_cache_entry(cache, url, b"")
    with pytest.raises(DatasetUnavailable):
        cache.fetch(url, min_bytes=256)


def test_cache_rejects_payload_below_floor(tmp_path):
    """A 234 KB landing page cannot pass as a 30-123 MB dataset payload."""
    cache = C.ContentAddressedCache(tmp_path)
    url = "https://zenodo.org/record/5510400"
    _seed_cache_entry(cache, url, b"x" * 1000)
    with pytest.raises(DatasetUnavailable):
        cache.fetch(url, min_bytes=1048576)


def test_cache_rejects_html_landing_page_by_content_type(tmp_path):
    """An HTML content-type is a landing/error page, not a dataset payload."""
    cache = C.ContentAddressedCache(tmp_path)
    url = "https://zenodo.org/record/5510400"
    _seed_cache_entry(cache, url, b"<!doctype html><html>...</html>" * 10, content_type="text/html; charset=utf-8")
    with pytest.raises(DatasetUnavailable):
        cache.fetch(url, min_bytes=1)


def test_cache_accepts_plausible_payload(tmp_path):
    """A payload clearing the floor with a non-HTML content-type is returned."""
    cache = C.ContentAddressedCache(tmp_path)
    url = "https://previous-runs-api.open-meteo.com/v1/forecast"
    body = b'{"hourly": {"time": []}}' + b" " * 512
    _seed_cache_entry(cache, url, body, content_type="application/json")
    entry = cache.fetch(url, min_bytes=256)
    assert entry.meta["observed_length"] == len(body)
    assert entry.read_bytes() == body


# --------------------------------------------------------------------------- loaders


def test_openmeteo_exact_lead_issue_derivation():
    from monsoon_grid.forecasting.datasets import openmeteo as OM

    payload = {
        "hourly": {
            "time": ["2024-06-03T00:00", "2024-06-03T01:00"],
            "shortwave_radiation_previous_day1": [0.0, 100.0],
        }
    }
    s = OM.parse_previous_runs_payload(payload, "shortwave_radiation", 1, model="ecmwf_ifs025")
    # issue = valid - 24h, exactly.
    assert (s.valid_time_epoch - s.issue_time_epoch == 86400).all()
    assert s.lead_minutes[0] == 1440.0
    assert s.as_issued_confidence == "previous_runs_exact_lead"


def test_gefcom_benchmark_spelling_resolver():
    from monsoon_grid.forecasting.datasets import gefcom2014 as G

    names_padded = ["Solar/Task 3/benchmark03.csv", "Solar/Task 3/train3.csv"]
    assert G.resolve_benchmark_member(names_padded, 3).endswith("benchmark03.csv")
    names_unpadded = ["Solar/Task 12/benchmark12.csv"]
    assert G.resolve_benchmark_member(names_unpadded, 12).endswith("benchmark12.csv")
    with pytest.raises(Exception):
        G.resolve_benchmark_member(["Solar/Task 5/train5.csv"], 5)


def test_gefcom_nested_zip_stored_and_deflated(tmp_path):
    import io
    import zipfile

    from monsoon_grid.forecasting.datasets import gefcom2014 as G

    # Build an inner zip, store it (method 0) inside an outer zip.
    inner_buf = io.BytesIO()
    with zipfile.ZipFile(inner_buf, "w", zipfile.ZIP_DEFLATED) as inner:
        inner.writestr("Task1_W_Zone1.csv", "ZONEID,TIMESTAMP,TARGETVAR\n1,20120101 01:00,0.5\n")
    outer_buf = io.BytesIO()
    with zipfile.ZipFile(outer_buf, "w") as outer:
        # STORED nested zip
        outer.writestr(zipfile.ZipInfo("Wind/Task 1/Task1_W_Zone1_10.zip"), inner_buf.getvalue(), zipfile.ZIP_STORED)
        # DEFLATED csv member
        outer.writestr("Wind/Task 1/benchmark1_W.csv", "a,b\n1,2\n")
    outer_buf.seek(0)
    with zipfile.ZipFile(outer_buf) as outer:
        names = G.list_members(outer)
        assert any(n.endswith("Task1_W_Zone1_10.zip") for n in names)
        nested = G.open_nested_zip(outer, "Wind/Task 1/Task1_W_Zone1_10.zip")
        assert "Task1_W_Zone1.csv" in G.list_members(nested)


def test_gefcom_depth_cap():
    from monsoon_grid.forecasting.datasets import gefcom2014 as G
    from monsoon_grid.forecasting.errors import DatasetLayoutError

    with pytest.raises(DatasetLayoutError):
        G.enforce_depth(4)
    G.enforce_depth(3)  # ok


def test_emsx_header_validation_and_melt():
    from monsoon_grid.forecasting.datasets import emsx as E

    header = E.EXPECTED_PREFIX + [f"load_{k:02d}" for k in range(96)] + [f"pv_{k:02d}" for k in range(96)]
    E.validate_header(header)  # no raise
    # One data row: timestamp, site, period, actual_cons, actual_pv, 96 loads, 96 pv.
    row = ["2015-06-12T00:00:00+00:00", "1", "5", "1000.0", "2000.0"]
    row += ["0.0"] * 96  # loads
    row += [str(100.0 * k) for k in range(96)]  # pv_k in kW
    melted = E.melt_rows([row])
    assert melted.issue_time_epoch.shape[0] == 96
    # valid_time for k=4 is issue + 4*15min.
    assert melted.valid_time_epoch[4] - melted.issue_time_epoch[4] == 4 * 900
    # kW -> MW conversion.
    assert melted.pv_forecast_mw[10] == pytest.approx(100.0 * 10 / 1000.0)
    assert melted.actual_pv_mw[0] == pytest.approx(2.0)


def test_emsx_bad_header_raises():
    from monsoon_grid.forecasting.datasets import emsx as E

    with pytest.raises(SchemaViolation):
        E.validate_header(["timestamp", "site_id"])  # too short / wrong


def test_emsx_melt_counting_pass_rejects_malformed_row():
    """Pass 1 (cheap counting pass) rejects a wrong-width row before allocation."""
    from monsoon_grid.forecasting.datasets import emsx as E
    from monsoon_grid.forecasting.errors import DatasetParseError

    good = ["2015-06-12T00:00:00+00:00", "1", "5", "1000.0", "2000.0"]
    good += ["0.0"] * 96 + [str(100.0 * k) for k in range(96)]
    bad = good[:-1]  # one field short
    with pytest.raises(DatasetParseError):
        E.melt_rows([good, bad])


def test_emsx_derived_capacity_training_block_only():
    from monsoon_grid.forecasting.datasets import emsx as E

    actual = np.array([1.0, 5.0, 3.0, 9.0])
    train = np.array([True, True, False, False])  # exclude the 9.0 (test block)
    assert E.derived_capacity_mw(actual, train) == pytest.approx(5.0)
