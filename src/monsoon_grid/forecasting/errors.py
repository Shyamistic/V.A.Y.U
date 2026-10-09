"""Error taxonomy for the forecasting subsystem (design §11.1).

Every class subclasses the product :class:`monsoon_grid.errors.MonsoonGridError`
so the later API layer sees one taxonomy. Each pins ``code``/``retryable``/
``recoverable`` exactly as tabulated in §11.1. "Recoverable" means the *caller*
has a defined degraded path (withhold a dataset's conclusions; emit a flagged
fallback band); it never means the library silently continues.
"""

from __future__ import annotations

from monsoon_grid.errors import MonsoonGridError


class ForecastingError(MonsoonGridError):
    """Base for all forecasting-subsystem errors."""

    code = "FORECASTING_ERROR"
    retryable = False
    recoverable = False


class DatasetUnavailable(ForecastingError):
    code = "FORECASTING_DATASET_UNAVAILABLE"
    retryable = True
    recoverable = True


class DatasetRequestRejected(ForecastingError):
    code = "FORECASTING_DATASET_REJECTED"
    retryable = False
    recoverable = True


class CacheIntegrityError(ForecastingError):
    code = "FORECASTING_CACHE_INTEGRITY"
    retryable = False
    recoverable = False


class DatasetTooLarge(ForecastingError):
    code = "FORECASTING_DATASET_TOO_LARGE"
    retryable = False
    recoverable = True


class DatasetLayoutError(ForecastingError):
    code = "FORECASTING_DATASET_LAYOUT"
    retryable = False
    recoverable = True


class DatasetParseError(ForecastingError):
    code = "FORECASTING_DATASET_PARSE"
    retryable = False
    recoverable = True


class DatasetQualityError(ForecastingError):
    code = "FORECASTING_DATASET_QUALITY"
    retryable = False
    recoverable = True


class SchemaViolation(ForecastingError):
    code = "FORECASTING_SCHEMA_VIOLATION"
    retryable = False
    recoverable = False


class ConfigError(ForecastingError):
    code = "FORECASTING_CONFIG_INVALID"
    retryable = False
    recoverable = False


class PhysicalConsistencyError(ForecastingError):
    code = "FORECASTING_PHYSICAL_CONSISTENCY"
    retryable = False
    recoverable = False


class QuantileFitError(ForecastingError):
    code = "FORECASTING_QUANTILE_FIT"
    retryable = False
    recoverable = True


class InsufficientData(ForecastingError):
    code = "FORECASTING_INSUFFICIENT_DATA"
    retryable = False
    recoverable = True


class PredictionError(ForecastingError):
    code = "FORECASTING_PREDICTION"
    retryable = False
    recoverable = False


class CalibrationUnavailable(ForecastingError):
    code = "FORECASTING_CALIBRATION_UNAVAILABLE"
    retryable = False
    recoverable = True


class DependenceFitError(ForecastingError):
    code = "FORECASTING_DEPENDENCE_FIT"
    retryable = False
    recoverable = False


class ScenarioError(ForecastingError):
    code = "FORECASTING_SCENARIO"
    retryable = False
    recoverable = False


class ArtifactNotFound(ForecastingError):
    code = "FORECASTING_ARTIFACT_NOT_FOUND"
    retryable = False
    recoverable = False


class ArtifactDigestMismatch(ForecastingError):
    code = "FORECASTING_ARTIFACT_DIGEST_MISMATCH"
    retryable = False
    recoverable = False


class ArtifactCorrupt(ForecastingError):
    code = "FORECASTING_ARTIFACT_CORRUPT"
    retryable = False
    recoverable = False


class ArtifactVersionUnsupported(ForecastingError):
    code = "FORECASTING_ARTIFACT_VERSION_UNSUPPORTED"
    retryable = False
    recoverable = False


class ArtifactConflict(ForecastingError):
    code = "FORECASTING_ARTIFACT_CONFLICT"
    retryable = False
    recoverable = False


class EvaluationProtocolError(ForecastingError):
    code = "FORECASTING_EVALUATION_PROTOCOL"
    retryable = False
    recoverable = False


class DeterminismError(ForecastingError):
    code = "FORECASTING_DETERMINISM"
    retryable = False
    recoverable = False


class ForecastingUnavailable(ForecastingError):
    code = "FORECASTING_UNAVAILABLE"
    retryable = False
    recoverable = True


__all__ = [
    "ForecastingError",
    "DatasetUnavailable",
    "DatasetRequestRejected",
    "CacheIntegrityError",
    "DatasetTooLarge",
    "DatasetLayoutError",
    "DatasetParseError",
    "DatasetQualityError",
    "SchemaViolation",
    "ConfigError",
    "PhysicalConsistencyError",
    "QuantileFitError",
    "InsufficientData",
    "PredictionError",
    "CalibrationUnavailable",
    "DependenceFitError",
    "ScenarioError",
    "ArtifactNotFound",
    "ArtifactDigestMismatch",
    "ArtifactCorrupt",
    "ArtifactVersionUnsupported",
    "ArtifactConflict",
    "EvaluationProtocolError",
    "DeterminismError",
    "ForecastingUnavailable",
]
