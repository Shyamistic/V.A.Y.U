"""Tests for the forecasting error taxonomy and strict base types (design §11.1, §9.1)."""

from __future__ import annotations

import sys

import pytest

from monsoon_grid.errors import MonsoonGridError
from monsoon_grid.forecasting import errors as ferr
from monsoon_grid.forecasting.types import Assumption, StrictRecord

# (class, code, retryable, recoverable) exactly as pinned in design §11.1.
EXPECTED = [
    (ferr.DatasetUnavailable, "FORECASTING_DATASET_UNAVAILABLE", True, True),
    (ferr.DatasetRequestRejected, "FORECASTING_DATASET_REJECTED", False, True),
    (ferr.CacheIntegrityError, "FORECASTING_CACHE_INTEGRITY", False, False),
    (ferr.DatasetTooLarge, "FORECASTING_DATASET_TOO_LARGE", False, True),
    (ferr.DatasetLayoutError, "FORECASTING_DATASET_LAYOUT", False, True),
    (ferr.DatasetParseError, "FORECASTING_DATASET_PARSE", False, True),
    (ferr.DatasetQualityError, "FORECASTING_DATASET_QUALITY", False, True),
    (ferr.SchemaViolation, "FORECASTING_SCHEMA_VIOLATION", False, False),
    (ferr.ConfigError, "FORECASTING_CONFIG_INVALID", False, False),
    (ferr.PhysicalConsistencyError, "FORECASTING_PHYSICAL_CONSISTENCY", False, False),
    (ferr.QuantileFitError, "FORECASTING_QUANTILE_FIT", False, True),
    (ferr.InsufficientData, "FORECASTING_INSUFFICIENT_DATA", False, True),
    (ferr.PredictionError, "FORECASTING_PREDICTION", False, False),
    (ferr.CalibrationUnavailable, "FORECASTING_CALIBRATION_UNAVAILABLE", False, True),
    (ferr.DependenceFitError, "FORECASTING_DEPENDENCE_FIT", False, False),
    (ferr.ScenarioError, "FORECASTING_SCENARIO", False, False),
    (ferr.ArtifactNotFound, "FORECASTING_ARTIFACT_NOT_FOUND", False, False),
    (ferr.ArtifactDigestMismatch, "FORECASTING_ARTIFACT_DIGEST_MISMATCH", False, False),
    (ferr.ArtifactCorrupt, "FORECASTING_ARTIFACT_CORRUPT", False, False),
    (ferr.ArtifactVersionUnsupported, "FORECASTING_ARTIFACT_VERSION_UNSUPPORTED", False, False),
    (ferr.ArtifactConflict, "FORECASTING_ARTIFACT_CONFLICT", False, False),
    (ferr.EvaluationProtocolError, "FORECASTING_EVALUATION_PROTOCOL", False, False),
    (ferr.DeterminismError, "FORECASTING_DETERMINISM", False, False),
    (ferr.ForecastingUnavailable, "FORECASTING_UNAVAILABLE", False, True),
]


@pytest.mark.parametrize("cls,code,retryable,recoverable", EXPECTED)
def test_error_codes_match_design(cls, code, retryable, recoverable):
    assert issubclass(cls, MonsoonGridError)
    assert cls.code == code
    assert cls.retryable is retryable
    assert cls.recoverable is recoverable


def test_error_instance_carries_code():
    exc = ferr.DatasetUnavailable("boom")
    assert exc.code == "FORECASTING_DATASET_UNAVAILABLE"
    assert exc.retryable is True
    assert str(exc) == "boom"


def test_all_24_classes_present():
    assert len(EXPECTED) == 24


def test_strict_record_rejects_extra_fields():
    class R(StrictRecord):
        x: int

    with pytest.raises(Exception):
        R(x=1, y=2)


def test_strict_record_rejects_non_finite_float():
    class R(StrictRecord):
        x: float

    with pytest.raises(Exception):
        R(x=float("nan"))
    with pytest.raises(Exception):
        R(x=float("inf"))


def test_strict_record_rejects_non_finite_in_nested_list():
    class R(StrictRecord):
        xs: list[float]

    with pytest.raises(Exception):
        R(xs=[1.0, float("inf")])


def test_strict_record_accepts_valid():
    class R(StrictRecord):
        x: float
        xs: list[float]

    r = R(x=1.5, xs=[0.0, -2.5])
    assert r.x == 1.5


def test_assumption_record():
    a = Assumption(name="albedo", value=0.2, unit="fraction", source="self-chosen", label="scenario_assumption")
    assert a.label == "scenario_assumption"


def test_package_import_is_light():
    # import the package fresh-ish and assert heavy deps are not pulled in by it.
    for mod in [m for m in list(sys.modules) if m.startswith("monsoon_grid.forecasting")]:
        del sys.modules[mod]
    had_scipy = "scipy" in sys.modules
    had_httpx = "httpx" in sys.modules
    import monsoon_grid.forecasting  # noqa: F401

    # If scipy/httpx were already imported by the test session we cannot assert
    # their absence; we only assert the package import does not NEWLY import them.
    if not had_scipy:
        assert "scipy" not in sys.modules
    if not had_httpx:
        assert "httpx" not in sys.modules
