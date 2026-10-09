"""Stable MonsoonGrid error categories."""

from __future__ import annotations


class MonsoonGridError(Exception):
    """Base product error with a stable machine-readable code."""

    code = "MONSOON_GRID_ERROR"
    retryable = False
    recoverable = False

    def __init__(self, message: str, *, code: str | None = None, retryable: bool | None = None, recoverable: bool | None = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        if recoverable is not None:
            self.recoverable = recoverable


class DependencyUnavailableError(MonsoonGridError):
    code = "DEPENDENCY_UNAVAILABLE"
    retryable = True


class InputMissingError(MonsoonGridError):
    code = "INPUT_MISSING"


class ReplayVersionMismatchError(MonsoonGridError):
    code = "REPLAY_VERSION_MISMATCH"


class TrustAnchorError(MonsoonGridError):
    code = "TRUST_ANCHOR_INVALID"


class CertificateError(MonsoonGridError):
    code = "CERTIFICATE_INVALID"


class StateConflictError(MonsoonGridError):
    code = "STATE_CONFLICT"
    retryable = True


class SafetyViolationError(MonsoonGridError):
    code = "SAFETY_REJECTED"


class BudgetExceededError(MonsoonGridError):
    code = "SOLVER_TIMEOUT"
    recoverable = True


class TwinInvariantError(MonsoonGridError):
    """Fatal twin accounting failure (energy balance or cost identity).

    The ``code`` is set at the raise site to either ``TWIN_INVARIANT_VIOLATION``
    or ``TWIN_COST_IDENTITY_VIOLATION``.
    """

    code = "TWIN_INVARIANT_VIOLATION"


class FixtureDigestError(MonsoonGridError):
    """A fixture's stored digest or a declared value does not match."""

    code = "FIXTURE_DIGEST_MISMATCH"


class MarketGateClosedError(MonsoonGridError):
    code = "MARKET_GATE_CLOSED"
    recoverable = False


class DsmInputError(MonsoonGridError):
    code = "DSM_ZERO_AVAILABLE_CAPACITY"


class IngestCacheMissError(MonsoonGridError):
    code = "INGEST_CACHE_MISS"
    recoverable = True


class IngestFetchError(MonsoonGridError):
    code = "INGEST_FETCH_FAILED"
    retryable = True


class CacheCorruptError(MonsoonGridError):
    code = "INGEST_CACHE_CORRUPT"
