"""MonsoonGrid observability: structured logging, metrics, readiness, audit.

Self-contained module (design §7). Depends only on the standard library plus the
pure product helpers ``canonical`` / ``config`` / ``certificates`` / ``errors`` and
``fastapi``/``starlette``. It imports nothing from ``api``/``cli``/``planner``/
``twin``/``validator`` so there is no import cycle and no behavioural coupling to
the replay engine.

Three concerns live here:
  * structured JSON logging in Cloud Logging shape, with allowlist redaction and
    W3C/Google trace correlation (§7.1);
  * a self-contained Prometheus-text metrics registry and the domain recording
    interfaces the planner/validator wiring will call (§7.2);
  * the operations router (liveness/readiness), SLO definitions, and the
    append-only hash-chained operational audit stream (§7.3-§7.5).

Observability must never be the reason a decision fails: every failure path here
is contained (§7.6).
"""

from __future__ import annotations

import contextvars
import datetime as dt
import importlib.util
import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence

from .canonical import sha256_hex

# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

REDACTED = "[redacted]"


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _rfc3339_micros(value: dt.datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    value = value.astimezone(dt.timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


# --------------------------------------------------------------------------- #
# §7.1 Structured logging, trace correlation, redaction
# --------------------------------------------------------------------------- #

# The explicit loggable-field allowlist. Any key not in this set is DROPPED (not
# masked) by redact_mapping, because a dropped field cannot leak and a masked
# field still reveals the field existed. Deliberately absent: seed, signing_key,
# private_key, token, authorization, secret, password, api_key.
LOGGABLE_FIELDS: frozenset[str] = frozenset(
    {
        "severity",
        "time",
        "message",
        "request_id",
        "trace_id",
        "span_id",
        "route",
        "method",
        "status",
        "latency_ms",
        "principal",
        "tenant",
        "replay_id",
        "decision_id",
        "certificate_id",
        "slot_index",
        "policy_mode",
        "scenario_id",
        "service",
        "revision",
        "logger",
        "scope",
        "event",
        "code",
        "reason",
        "rate_class",
        "required_role",
        "violation_code",
        "fallback_reason",
        "solver_status",
        "stage_name",
        "objective_name",
        "auth_method",
        "token_subject",
        "token_fingerprint",
        "approval_binding_digest",
        "state_digest",
        "unsigned_digest",
        "source_tree_digest",
        "tenant_source",
        "count",
        "seconds",
        "bytes",
        "attempt",
    }
)

# Keys that are hard-blocked by name regardless of the allowlist (belt and braces).
_HARD_BLOCKED_KEYS: frozenset[str] = frozenset(
    {"seed", "signing_key", "private_key", "token", "authorization", "secret", "password", "api_key"}
)

# The four generic keys that must carry numbers only; a non-numeric value is
# redacted by the type check rather than passed through.
_NUMERIC_ONLY_KEYS: frozenset[str] = frozenset({"count", "seconds", "bytes", "attempt"})

_BEARER_RE = re.compile(r"Bearer\s+\S+")
_JWT_RE = re.compile(r"^ey[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.")
_PEM_RE = re.compile(r"-----BEGIN [A-Z ]*(PRIVATE KEY|CERTIFICATE)")


def _value_looks_secret(value: str) -> bool:
    return bool(_BEARER_RE.search(value) or _JWT_RE.search(value) or _PEM_RE.search(value))


def redact_mapping(values: Mapping[str, Any]) -> dict[str, Any]:
    """Allowlist-driven redaction (§7.1, AC-18).

    Drops any key not in LOGGABLE_FIELDS and any hard-blocked key. For the four
    generic numeric keys, a non-numeric value is replaced with REDACTED by a type
    check. A value-pattern backstop replaces Bearer/JWT/PEM-shaped strings. A bare
    64-char hex digest is left intact because digests are evidence, not secrets.
    """
    out: dict[str, Any] = {}
    for key, value in values.items():
        if key in _HARD_BLOCKED_KEYS or key not in LOGGABLE_FIELDS:
            continue
        if key in _NUMERIC_ONLY_KEYS:
            out[key] = value if isinstance(value, (int, float)) and not isinstance(value, bool) else REDACTED
            continue
        if isinstance(value, str) and _value_looks_secret(value):
            out[key] = REDACTED
            continue
        out[key] = value
    return out


@dataclass
class RequestContext:
    request_id: str
    trace_id: str | None
    span_id: str | None
    trace_sampled: bool
    route: str
    method: str
    principal: str | None
    tenant: str | None
    started_monotonic: float
    replay_id: str | None = None
    decision_id: str | None = None
    certificate_id: str | None = None
    slot_index: int | None = None


_CONTEXT: contextvars.ContextVar[RequestContext | None] = contextvars.ContextVar("monsoon_request_context", default=None)

_LOGGING_SETTINGS: dict[str, Any] = {"gcp_project_id": None, "service": "monsoongrid", "revision": "unknown"}
_FORMAT_FAILURE_LAST: dict[str, float] = {}


def current_context() -> RequestContext | None:
    return _CONTEXT.get()


def set_context(context: RequestContext | None) -> contextvars.Token:
    return _CONTEXT.set(context)


def reset_context(token: contextvars.Token) -> None:
    _CONTEXT.reset(token)


def bind_domain(
    *,
    replay_id: str | None = None,
    decision_id: str | None = None,
    slot_index: int | None = None,
    certificate_id: str | None = None,
) -> None:
    """Attach domain identifiers to the current request context so every later
    log record in this request correlates (§7.1)."""
    context = _CONTEXT.get()
    if context is None:
        return
    if replay_id is not None:
        context.replay_id = replay_id
    if decision_id is not None:
        context.decision_id = decision_id
    if slot_index is not None:
        context.slot_index = slot_index
    if certificate_id is not None:
        context.certificate_id = certificate_id


_TRACEPARENT_RE = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
_CLOUD_TRACE_RE = re.compile(r"^([0-9a-f]{32})/(\d+)(?:;o=([01]))?$", re.IGNORECASE)


def parse_trace_headers(headers: Mapping[str, str]) -> tuple[str | None, str | None, bool]:
    """Return (trace_id, span_id, sampled). W3C traceparent first, then Google
    X-Cloud-Trace-Context, then generate a fresh 32-hex trace id (§7.1, AC-21)."""
    lowered = {str(k).lower(): v for k, v in headers.items()}

    traceparent = lowered.get("traceparent", "")
    match = _TRACEPARENT_RE.match(traceparent.strip())
    if match:
        trace_id, span_id, flags = match.group(1), match.group(2), match.group(3)
        return trace_id, span_id, bool(int(flags, 16) & 0x01)

    cloud = lowered.get("x-cloud-trace-context", "")
    match = _CLOUD_TRACE_RE.match(cloud.strip())
    if match:
        trace_id = match.group(1).lower()
        span_dec = int(match.group(2))
        span_id = f"{span_dec & 0xFFFFFFFFFFFFFFFF:016x}"
        sampled = match.group(3) == "1"
        return trace_id, span_id, sampled

    return secrets.token_hex(16), None, False


class JsonLogFormatter(logging.Formatter):
    """One-line JSON object on stdout in Cloud Logging structured-payload shape."""

    def __init__(self, *, gcp_project_id: str | None, service: str, revision: str) -> None:
        super().__init__()
        self.gcp_project_id = gcp_project_id
        self.service = service
        self.revision = revision

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003 - stdlib signature
        try:
            return self._format(record)
        except Exception:  # pragma: no cover - defensive, §7.6
            now = time.time()
            last = _FORMAT_FAILURE_LAST.get(record.name, 0.0)
            if now - last > 60.0:
                _FORMAT_FAILURE_LAST[record.name] = now
            return json.dumps(
                {"severity": "ERROR", "message": "log_format_failed", "logger": record.name},
                separators=(",", ":"),
            )

    def _format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "severity": record.levelname,
            "time": _rfc3339_micros(dt.datetime.fromtimestamp(record.created, dt.timezone.utc)),
            "message": record.getMessage(),
            "logger": record.name,
            "service": self.service,
            "revision": self.revision,
            "scope": "REPLAY",
        }

        context = _CONTEXT.get()
        if context is not None:
            if self.gcp_project_id and context.trace_id:
                payload["logging.googleapis.com/trace"] = f"projects/{self.gcp_project_id}/traces/{context.trace_id}"
            if context.span_id:
                payload["logging.googleapis.com/spanId"] = context.span_id
            if context.trace_sampled:
                payload["logging.googleapis.com/trace_sampled"] = True
            payload["request_id"] = context.request_id
            payload["route"] = context.route
            payload["method"] = context.method
            if context.principal is not None:
                payload["principal"] = context.principal
            if context.tenant is not None:
                payload["tenant"] = context.tenant
            if context.replay_id is not None:
                payload["replay_id"] = context.replay_id
            if context.decision_id is not None:
                payload["decision_id"] = context.decision_id
            if context.slot_index is not None:
                payload["slot_index"] = context.slot_index
            if context.certificate_id is not None:
                payload["certificate_id"] = context.certificate_id

        extra = getattr(record, "monsoon_fields", None)
        if isinstance(extra, Mapping):
            payload.update(redact_mapping(extra))

        for key in ("status", "latency_ms"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value

        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=str)


def install_logging(
    *,
    level: str = "INFO",
    gcp_project_id: str | None = None,
    service: str = "monsoongrid",
    revision: str = "unknown",
) -> None:
    """Install the JSON formatter on the root logger's stdout handler (§7.1)."""
    _LOGGING_SETTINGS.update(gcp_project_id=gcp_project_id, service=service, revision=revision)
    formatter = JsonLogFormatter(gcp_project_id=gcp_project_id, service=service, revision=revision)
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root.addHandler(handler)


def log_event(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    """Emit a structured record carrying extra fields through the redaction allowlist."""
    status = fields.pop("status", None)
    latency_ms = fields.pop("latency_ms", None)
    logger.log(level, message, extra={"monsoon_fields": fields, "status": status, "latency_ms": latency_ms})


# --------------------------------------------------------------------------- #
# §7.2 Metrics registry (Prometheus text exposition v0.0.4)
# --------------------------------------------------------------------------- #

_MAX_LABEL_LEN = 64
_MAX_SERIES_PER_INSTRUMENT = 2000


class _Instrument:
    def __init__(self, name: str, help_text: str, labels: Sequence[str], registry: "MetricsRegistry") -> None:
        self.name = name
        self.help = help_text
        self.labels = tuple(labels)
        self._registry = registry
        self._lock = threading.Lock()

    def _key(self, labels: Mapping[str, str]) -> tuple[str, ...] | None:
        if set(labels) != set(self.labels):
            raise ValueError(f"instrument {self.name} expects labels {self.labels}, got {tuple(labels)}")
        values: list[str] = []
        for label in self.labels:
            value = str(labels[label])
            if len(value) > _MAX_LABEL_LEN:
                self._registry._drop_cardinality(self.name)
                return None
            values.append(value)
        return tuple(values)


class Counter(_Instrument):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._values: dict[tuple[str, ...], float] = {}

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        key = self._key(labels)
        if key is None:
            return
        with self._lock:
            if key not in self._values and len(self._values) >= _MAX_SERIES_PER_INSTRUMENT:
                self._registry._drop_cardinality(self.name)
                return
            self._values[key] = self._values.get(key, 0.0) + amount


class Gauge(_Instrument):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._values: dict[tuple[str, ...], float] = {}

    def set(self, value: float, **labels: str) -> None:
        key = self._key(labels)
        if key is None:
            return
        with self._lock:
            if key not in self._values and len(self._values) >= _MAX_SERIES_PER_INSTRUMENT:
                self._registry._drop_cardinality(self.name)
                return
            self._values[key] = float(value)


class Histogram(_Instrument):
    def __init__(self, name: str, help_text: str, buckets: Sequence[float], labels: Sequence[str], registry: "MetricsRegistry") -> None:
        super().__init__(name, help_text, labels, registry)
        self.buckets = tuple(sorted(float(b) for b in buckets))
        self._counts: dict[tuple[str, ...], list[int]] = {}
        self._sums: dict[tuple[str, ...], float] = {}
        self._totals: dict[tuple[str, ...], int] = {}

    def observe(self, value: float, **labels: str) -> None:
        key = self._key(labels)
        if key is None:
            return
        with self._lock:
            if key not in self._counts and len(self._counts) >= _MAX_SERIES_PER_INSTRUMENT:
                self._registry._drop_cardinality(self.name)
                return
            counts = self._counts.setdefault(key, [0] * len(self.buckets))
            # exclusive per-bucket counts; render_prometheus accumulates them.
            for i, bound in enumerate(self.buckets):
                if value <= bound:
                    counts[i] += 1
                    break
            self._sums[key] = self._sums.get(key, 0.0) + float(value)
            self._totals[key] = self._totals.get(key, 0) + 1


class MetricsRegistry:
    def __init__(self) -> None:
        self._instruments: dict[str, _Instrument] = {}
        self._lock = threading.Lock()
        self._cardinality_dropped: Counter | None = None

    def _register(self, instrument: _Instrument) -> _Instrument:
        with self._lock:
            existing = self._instruments.get(instrument.name)
            if existing is not None:
                return existing
            self._instruments[instrument.name] = instrument
            return instrument

    def counter(self, name: str, help: str, labels: Sequence[str] = ()) -> Counter:
        return self._register(Counter(name, help, labels, self))  # type: ignore[return-value]

    def gauge(self, name: str, help: str, labels: Sequence[str] = ()) -> Gauge:
        return self._register(Gauge(name, help, labels, self))  # type: ignore[return-value]

    def histogram(self, name: str, help: str, buckets: Sequence[float], labels: Sequence[str] = ()) -> Histogram:
        return self._register(Histogram(name, help, buckets, labels, self))  # type: ignore[return-value]

    def get(self, name: str) -> _Instrument | None:
        return self._instruments.get(name)

    def _drop_cardinality(self, metric: str) -> None:
        if self._cardinality_dropped is not None and metric != self._cardinality_dropped.name:
            self._cardinality_dropped.inc(metric=metric)

    def render_prometheus(self) -> str:
        lines: list[str] = []
        for name in sorted(self._instruments):
            inst = self._instruments[name]
            if isinstance(inst, Counter):
                lines.append(f"# HELP {name} {inst.help}")
                lines.append(f"# TYPE {name} counter")
                for key, value in sorted(inst._values.items()):
                    lines.append(f"{name}{_labels(inst.labels, key)} {_num(value)}")
            elif isinstance(inst, Gauge):
                lines.append(f"# HELP {name} {inst.help}")
                lines.append(f"# TYPE {name} gauge")
                for key, value in sorted(inst._values.items()):
                    lines.append(f"{name}{_labels(inst.labels, key)} {_num(value)}")
            elif isinstance(inst, Histogram):
                lines.append(f"# HELP {name} {inst.help}")
                lines.append(f"# TYPE {name} histogram")
                for key in sorted(inst._counts):
                    counts = inst._counts[key]
                    cumulative = 0
                    label_pairs = dict(zip(inst.labels, key))
                    for i, bound in enumerate(inst.buckets):
                        cumulative += counts[i]
                        pairs = {**label_pairs, "le": _num(bound)}
                        lines.append(f"{name}_bucket{_labels_from_dict(pairs)} {cumulative}")
                    inf_pairs = {**label_pairs, "le": "+Inf"}
                    lines.append(f"{name}_bucket{_labels_from_dict(inf_pairs)} {inst._totals[key]}")
                    lines.append(f"{name}_sum{_labels(inst.labels, key)} {_num(inst._sums[key])}")
                    lines.append(f"{name}_count{_labels(inst.labels, key)} {inst._totals[key]}")
        return "\n".join(lines) + ("\n" if lines else "")

    def snapshot(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, inst in self._instruments.items():
            if isinstance(inst, (Counter, Gauge)):
                out[name] = {"|".join(k): v for k, v in inst._values.items()}
            elif isinstance(inst, Histogram):
                out[name] = {"|".join(k): {"count": inst._totals[k], "sum": inst._sums[k]} for k in inst._totals}
        return out


def _num(value: float) -> str:
    if value == float("inf"):
        return "+Inf"
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))


def _esc(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _labels(label_names: Sequence[str], key: tuple[str, ...]) -> str:
    if not label_names:
        return ""
    return _labels_from_dict(dict(zip(label_names, key)))


def _labels_from_dict(pairs: Mapping[str, str]) -> str:
    if not pairs:
        return ""
    inner = ",".join(f'{name}="{_esc(str(value))}"' for name, value in pairs.items())
    return "{" + inner + "}"


# --------------------------------------------------------------------------- #
# Instrument catalogue (§7.2, AC-24)
# --------------------------------------------------------------------------- #

_REQUEST_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300)
_PLANNER_STAGE_BUCKETS = (0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10)

_DEFAULT_REGISTRY: MetricsRegistry | None = None


def install_metrics(registry: MetricsRegistry | None = None) -> MetricsRegistry:
    """Create (or reuse) the default registry and register every catalogued
    instrument by name/type/labels (§7.2, AC-24)."""
    global _DEFAULT_REGISTRY
    reg = registry or MetricsRegistry()

    reg.counter("monsoongrid_requests_total", "HTTP requests", ("route", "method", "status", "status_class"))
    reg.histogram("monsoongrid_request_latency_seconds", "Request latency", _REQUEST_LATENCY_BUCKETS, ("route", "method"))
    reg.counter("monsoongrid_errors_total", "Errors by route and code", ("route", "code"))
    reg.histogram(
        "monsoongrid_planner_stage_latency_seconds",
        "Planner lexicographic stage latency",
        _PLANNER_STAGE_BUCKETS,
        ("policy_mode", "stage_name", "objective_name", "status", "latency_source"),
    )
    reg.counter(
        "monsoongrid_planner_stage_latency_unreported_total",
        "Planner stage reported a 0.0 duration",
        ("policy_mode", "stage_name"),
    )
    reg.histogram(
        "monsoongrid_planner_solve_latency_seconds",
        "Planner solve latency",
        _PLANNER_STAGE_BUCKETS,
        ("policy_mode", "solver_name", "status"),
    )
    reg.counter("monsoongrid_planner_fallback_total", "Fallback plans", ("policy_mode", "fallback_reason"))
    reg.counter("monsoongrid_validator_rejections_total", "Validator rejections", ("violation_code", "severity"))
    reg.counter("monsoongrid_validator_decisions_total", "Validator decisions", ("status",))
    reg.counter("monsoongrid_escalations_total", "Escalations", ("reason",))
    reg.counter(
        "monsoongrid_certificate_operations_total",
        "Certificate operations",
        ("operation", "outcome", "failure_code"),
    )
    reg.counter("monsoongrid_data_quality_total", "Data quality decisions", ("signal_class", "code", "effect"))
    reg.counter("monsoongrid_auth_failures_total", "Authentication failures", ("reason",))
    reg.counter("monsoongrid_authz_denials_total", "Authorization denials", ("route", "required_role"))
    reg.counter("monsoongrid_approval_rejections_total", "Approval rejections", ("code",))
    reg.counter("monsoongrid_approvals_total", "Approvals", ("action", "separation_of_duty", "author_class"))
    reg.counter("monsoongrid_rate_limited_total", "Rate-limited requests", ("rate_class", "scope"))
    reg.counter("monsoongrid_tenant_denials_total", "Tenant scope denials", ("route",))
    reg.counter("monsoongrid_cadence_skips_total", "Cadence skips", ("loop", "reason"))
    reg.counter("monsoongrid_body_limit_rejections_total", "Body-limit rejections", ("route",))
    reg.counter("monsoongrid_request_timeouts_total", "Request timeouts", ("route",))
    reg.counter("monsoongrid_jwks_refresh_failures_total", "JWKS refresh failures", ("reason",))
    reg.gauge("monsoongrid_jwks_keys", "JWKS keys currently cached")
    reg.gauge("monsoongrid_readiness_state", "Readiness per component (0/1/2)", ("component",))
    reg.counter("monsoongrid_audit_records_total", "Audit records", ("event_type",))
    reg.gauge("monsoongrid_audit_chain_verified", "Last audit chain verify result (0/1)")
    reg.gauge(
        "monsoongrid_build_info",
        "Build info",
        ("revision", "source_tree_digest", "python_version", "scipy_version"),
    )
    reg.counter("monsoongrid_metric_cardinality_dropped_total", "Dropped observations", ("metric",))

    reg._cardinality_dropped = reg.get("monsoongrid_metric_cardinality_dropped_total")  # type: ignore[assignment]

    if registry is None:
        _DEFAULT_REGISTRY = reg
    elif _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = reg
    return reg


def default_registry() -> MetricsRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = install_metrics()
    return _DEFAULT_REGISTRY


# --------------------------------------------------------------------------- #
# Domain recording interfaces (§7.2) — the planner/validator wiring surface
# --------------------------------------------------------------------------- #


def record_planner_stage(
    *,
    policy_mode: str,
    stage_name: str,
    objective_name: str,
    seconds: float,
    status: str,
    latency_source: Literal["measured_at_boundary", "reported_by_planner"],
) -> None:
    reg = default_registry()
    hist = reg.get("monsoongrid_planner_stage_latency_seconds")
    if isinstance(hist, Histogram):
        hist.observe(
            seconds,
            policy_mode=policy_mode,
            stage_name=stage_name,
            objective_name=objective_name,
            status=status,
            latency_source=latency_source,
        )
    if latency_source == "reported_by_planner" and seconds == 0.0:
        counter = reg.get("monsoongrid_planner_stage_latency_unreported_total")
        if isinstance(counter, Counter):
            counter.inc(policy_mode=policy_mode, stage_name=stage_name)


def record_solver_result(
    *,
    policy_mode: str,
    solver_name: str,
    status: str,
    seconds: float,
    fallback_reason: str | None,
    latency_source: str,
) -> None:
    reg = default_registry()
    hist = reg.get("monsoongrid_planner_solve_latency_seconds")
    if isinstance(hist, Histogram):
        hist.observe(seconds, policy_mode=policy_mode, solver_name=solver_name, status=status)
    if fallback_reason:
        counter = reg.get("monsoongrid_planner_fallback_total")
        if isinstance(counter, Counter):
            counter.inc(policy_mode=policy_mode, fallback_reason=fallback_reason)


def record_safety_decision(
    *,
    status: str,
    violations: Iterable[tuple[str, str]],
    critical_unserved_mwh: float,
    reserve_shortfall_mwh: float,
) -> None:
    reg = default_registry()
    decisions = reg.get("monsoongrid_validator_decisions_total")
    if isinstance(decisions, Counter):
        decisions.inc(status=status)
    rejections = reg.get("monsoongrid_validator_rejections_total")
    if isinstance(rejections, Counter):
        for code, severity in violations:
            rejections.inc(violation_code=code, severity=severity)
    if status == "ESCALATED_NO_SAFE_ACTION":
        escalations = reg.get("monsoongrid_escalations_total")
        if isinstance(escalations, Counter):
            escalations.inc(reason="no_safe_action")


def record_certificate_operation(*, operation: str, outcome: str, failure_code: str | None = None) -> None:
    reg = default_registry()
    counter = reg.get("monsoongrid_certificate_operations_total")
    if isinstance(counter, Counter):
        counter.inc(operation=operation, outcome=outcome, failure_code=failure_code or "none")


def record_quality_decision(*, signal_class: str, code: str, effect: str) -> None:
    reg = default_registry()
    counter = reg.get("monsoongrid_data_quality_total")
    if isinstance(counter, Counter):
        counter.inc(signal_class=signal_class, code=code, effect=effect)


# --------------------------------------------------------------------------- #
# §7.3 Health and readiness
# --------------------------------------------------------------------------- #

ComponentName = Literal["database", "trust", "solver", "audit_sink"]
ComponentState = Literal["ok", "degraded", "unavailable"]

_READINESS_STATE = {"ok": 2, "degraded": 1, "unavailable": 0}
_PROCESS_STARTED = time.monotonic()
_SOLVER_PROBE_CACHE: dict[str, Any] = {"result": None, "at": 0.0}
_SOLVER_PROBE_TTL = 300.0


@dataclass(frozen=True)
class ComponentHealth:
    component: ComponentName
    state: ComponentState
    detail: str
    checked_in_ms: float


def _ok(component: ComponentName, detail: str, ms: float = 0.0) -> ComponentHealth:
    return ComponentHealth(component=component, state="ok", detail=detail, checked_in_ms=ms)


def _degraded(component: ComponentName, detail: str, ms: float = 0.0) -> ComponentHealth:
    return ComponentHealth(component=component, state="degraded", detail=detail, checked_in_ms=ms)


def _unavailable(component: ComponentName, detail: str, ms: float = 0.0) -> ComponentHealth:
    return ComponentHealth(component=component, state="unavailable", detail=detail, checked_in_ms=ms)


def check_trust() -> ComponentHealth:
    """Five-branch trust decision tree (§7.3, AC-27a). No response body ever
    includes the seed, the key path, or exception text."""
    from . import certificates
    from .config import Settings

    start = time.perf_counter()
    settings = Settings.from_env()
    try:
        keyring_key, _public = certificates._trusted_key(settings)
    except Exception:
        return _unavailable("trust", "trust_anchor_invalid", (time.perf_counter() - start) * 1000)

    if not os.environ.get("MONSOON_GRID_SIGNING_KEY"):
        return _degraded("trust", "verify_only_no_signing_key", (time.perf_counter() - start) * 1000)

    try:
        certificates._private_key(settings, keyring_key)
    except Exception:
        return _unavailable("trust", "signing_key_unusable", (time.perf_counter() - start) * 1000)

    return _ok("trust", "signing_and_verification_available", (time.perf_counter() - start) * 1000)


def check_solver() -> ComponentHealth:
    """SciPy HiGHS smoke solve cached for 300 s; degrade to fallback if milp is
    unavailable but the deterministic fallback import works (§7.3, AC-27)."""
    now = time.monotonic()
    cached = _SOLVER_PROBE_CACHE
    if cached["result"] is not None and (now - cached["at"]) < _SOLVER_PROBE_TTL:
        return cached["result"]

    start = time.perf_counter()
    result: ComponentHealth
    try:
        import numpy as np
        from scipy.optimize import Bounds, LinearConstraint, milp

        solved = milp(
            c=np.array([1.0, 1.0]),
            integrality=np.array([1, 1]),
            bounds=Bounds(np.array([0.0, 0.0]), np.array([5.0, 5.0])),
            constraints=LinearConstraint(np.array([[1.0, 1.0]]), 2.0, 2.0),
        )
        if solved.success:
            result = _ok("solver", "highs_milp_ok", (time.perf_counter() - start) * 1000)
        else:
            result = _solver_fallback_state(start)
    except Exception:
        result = _solver_fallback_state(start)

    _SOLVER_PROBE_CACHE["result"] = result
    _SOLVER_PROBE_CACHE["at"] = now
    return result


def _solver_fallback_state(start: float) -> ComponentHealth:
    if importlib.util.find_spec("monsoon_grid.fallback") is not None:
        return _degraded("solver", "milp_unavailable_fallback_usable", (time.perf_counter() - start) * 1000)
    return _unavailable("solver", "no_solver_and_no_fallback", (time.perf_counter() - start) * 1000)


def _reset_solver_probe_cache() -> None:
    _SOLVER_PROBE_CACHE["result"] = None
    _SOLVER_PROBE_CACHE["at"] = 0.0


def check_database() -> ComponentHealth:
    start = time.perf_counter()
    try:
        from .persistence import PersistenceStore

        store = PersistenceStore()
        with store._connect() as conn:
            conn.execute("SELECT 1")
        return _ok("database", "reachable", (time.perf_counter() - start) * 1000)
    except Exception as exc:
        return _unavailable("database", type(exc).__name__, (time.perf_counter() - start) * 1000)


def check_audit_sink(audit: "AuditLog | None" = None) -> ComponentHealth:
    start = time.perf_counter()
    log = audit or _default_audit()
    try:
        if log.writable():
            return _ok("audit_sink", "writable", (time.perf_counter() - start) * 1000)
        return _degraded("audit_sink", "buffered_only", (time.perf_counter() - start) * 1000)
    except Exception as exc:
        return _unavailable("audit_sink", type(exc).__name__, (time.perf_counter() - start) * 1000)


def liveness() -> dict[str, Any]:
    return {
        "status": "ok",
        "revision": _LOGGING_SETTINGS.get("revision", "unknown"),
        "uptime_seconds": round(time.monotonic() - _PROCESS_STARTED, 3),
    }


def readiness(*, checks: Sequence[Callable[[], ComponentHealth]] | None = None) -> tuple[int, dict[str, Any]]:
    probes = checks or (check_database, check_trust, check_solver, check_audit_sink)
    components: list[ComponentHealth] = []
    for probe in probes:
        try:
            components.append(probe())
        except Exception as exc:  # §7.6
            components.append(ComponentHealth("database", "unavailable", type(exc).__name__, 0.0))

    reg = default_registry()
    gauge = reg.get("monsoongrid_readiness_state")
    if isinstance(gauge, Gauge):
        for comp in components:
            gauge.set(_READINESS_STATE[comp.state], component=comp.component)

    states = {comp.state for comp in components}
    if "unavailable" in states:
        http_status, status = 503, "unready"
    elif "degraded" in states:
        http_status, status = 200, "degraded"
    else:
        http_status, status = 200, "ready"

    solver = next((c for c in components if c.component == "solver"), None)
    body: dict[str, Any] = {
        "status": status,
        "components": [
            {"component": c.component, "state": c.state, "detail": c.detail, "checked_in_ms": round(c.checked_in_ms, 3)}
            for c in components
        ],
    }
    if solver is not None:
        body["solver"] = "unavailable" if solver.state != "ok" else "available"
    return http_status, body


# --------------------------------------------------------------------------- #
# §7.4 SLOs and alerting
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SloDefinition:
    slo_id: str
    title: str
    objective: str
    target: float
    window: str
    numerator: str
    denominator: str
    rationale: str
    data_source: str
    attained: None = None
    measured: bool = False


def slo_definitions() -> tuple[SloDefinition, ...]:
    return (
        SloDefinition(
            slo_id="S1",
            title="Decision availability",
            objective=">= 99.0% of scheduled slots produce a validated, signed decision",
            target=0.99,
            window="rolling 28d",
            numerator="scheduled 15-min slots that produced a validated SafetyDecision with a signed certificate",
            denominator="scheduled slots",
            rationale="96 slots/day x 28d = 2688 valid events; 99.0% allows ~26 misses, roughly one per day; 99.9% is undefensible for a single-revision prototype without history.",
            data_source="log-based metric over decision log records",
        ),
        SloDefinition(
            slo_id="S2",
            title="Decision latency",
            objective="p95 <= 60s, p99 <= 285s from scheduler invocation to persistence",
            target=60.0,
            window="rolling 7d",
            numerator="latency from scheduler invocation to decision persisted",
            denominator="all planned slots",
            rationale="Cadence is 900s; p95 of 60s leaves 15x headroom; the p99 ceiling is REPLAY_BUDGET_SECONDS=285 from config.py, the product's own budget.",
            data_source="request latency histogram on /planning and the scheduler adapter",
        ),
        SloDefinition(
            slo_id="S3",
            title="Fallback-rate ceiling",
            objective="<= 5% of slots use the deterministic fallback",
            target=0.05,
            window="rolling 7d",
            numerator="slots whose SolverResult.status == OPTIMAL",
            denominator="all planned slots",
            rationale="The fallback is a safe documented rule table, so this is a quality objective; 5% ~ 5 slots/day; alert at >10% in 1h catches a HiGHS outage within an hour.",
            data_source="planner_fallback_total vs validator_decisions_total",
        ),
        SloDefinition(
            slo_id="S4",
            title="Certificate verification success",
            objective="100% of verifications succeed",
            target=1.0,
            window="every verification",
            numerator="load_and_verify() returning verified=True",
            denominator="every verification attempt",
            rationale="This is an integrity objective, not availability; a single failure means the evidence chain is wrong and trust must stop, hence a page not an error budget.",
            data_source="certificate_operations_total{operation=verify}",
        ),
    )


# --------------------------------------------------------------------------- #
# §7.5 Operational audit stream (append-only SHA-256 hash chain)
# --------------------------------------------------------------------------- #

AUDIT_SCHEMA = "monsoongrid-audit-v1"
GENESIS_PREV_HASH = "0" * 64

AuditEventType = Literal[
    "auth_failure",
    "authz_denied",
    "tenant_scope_denied",
    "approval_recorded",
    "approval_rejected",
    "override_recorded",
    "escalation_recorded",
    "config_changed",
    "config_degraded",
    "rate_limit_sustained",
    "internal_error",
    "startup",
    "shutdown",
]


class AuditLog:
    """Append-only JSONL audit stream with a SHA-256 hash chain (§7.5).

    Tamper-EVIDENT, not tamper-proof: an attacker who can rewrite the file and
    recompute hashes can rebuild the chain. The real controls are shipping the
    head off-instance (stdout -> Cloud Logging) and bucket-lock retention; the
    chain is a cheap integrity check.
    """

    def __init__(
        self,
        *,
        path: Path | None = None,
        sink: Callable[[str], None] | None = None,
        clock: Callable[[], dt.datetime] = _utcnow,
    ) -> None:
        self._path = path
        self._sink = sink
        self._clock = clock
        self._lock = threading.Lock()
        self._seq = 0
        self._head = GENESIS_PREV_HASH
        self._buffer: list[str] = []
        self._logger = logging.getLogger("monsoon_grid.audit")
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                self._resume(path)

    def _resume(self, path: Path) -> None:
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            record = json.loads(raw)
            self._seq = int(record["seq"]) + 1
            self._head = record["entry_hash"]

    def writable(self) -> bool:
        if self._path is None:
            return self._sink is not None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            return os.access(self._path.parent, os.W_OK)
        except OSError:
            return False

    def head(self) -> str:
        return self._head

    def append(
        self,
        *,
        event_type: AuditEventType,
        outcome: str,
        principal: str | None,
        tenant: str | None,
        request_id: str,
        trace_id: str | None,
        target: Mapping[str, str] | None = None,
        detail: Mapping[str, str | int | float | bool] | None = None,
    ) -> str:
        with self._lock:
            record: dict[str, Any] = {
                "schema": AUDIT_SCHEMA,
                "seq": self._seq,
                "recorded_at": _rfc3339_micros(self._clock()),
                "event_type": event_type,
                "outcome": outcome,
                "principal": principal,
                "tenant": tenant,
                "request_id": request_id,
                "trace_id": trace_id,
                "target": dict(target) if target else {},
                "detail": dict(detail) if detail else {},
                "prev_hash": self._head,
                "audit": True,
            }
            entry_hash = sha256_hex(record)
            record["entry_hash"] = entry_hash
            line = json.dumps(record, separators=(",", ":"), sort_keys=True)

            wrote = False
            if self._path is not None:
                try:
                    with open(self._path, "a", encoding="utf-8") as handle:
                        handle.write(line + "\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                    wrote = True
                except OSError:
                    self._buffer.append(line)
                    if len(self._buffer) > 1000:
                        self._buffer.pop(0)
            if self._sink is not None:
                self._sink(line)
                wrote = True
            # Always surface to stdout so Cloud Logging captures it.
            self._logger.info(line)

            self._head = entry_hash
            self._seq += 1

            reg = default_registry()
            counter = reg.get("monsoongrid_audit_records_total")
            if isinstance(counter, Counter):
                counter.inc(event_type=event_type)

            if not wrote and self._path is not None and not self._buffer:
                # buffered path, degraded but recorded
                pass
            return entry_hash

    def verify(self, *, path: Path | None = None) -> dict[str, Any]:
        target = path or self._path
        if target is None or not target.exists():
            return {"ok": True, "records": 0, "first_broken_seq": None, "head": GENESIS_PREV_HASH}
        prev = GENESIS_PREV_HASH
        records = 0
        first_broken: int | None = None
        for raw in target.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            records += 1
            record = json.loads(raw)
            stored = record.get("entry_hash")
            without = {k: v for k, v in record.items() if k != "entry_hash"}
            recomputed = sha256_hex(without)
            if record.get("prev_hash") != prev or recomputed != stored:
                first_broken = int(record.get("seq", records - 1))
                break
            prev = stored
        ok = first_broken is None

        reg = default_registry()
        gauge = reg.get("monsoongrid_audit_chain_verified")
        if isinstance(gauge, Gauge):
            gauge.set(1 if ok else 0)

        return {"ok": ok, "records": records, "first_broken_seq": first_broken, "head": prev}


_DEFAULT_AUDIT: AuditLog | None = None


def _default_audit() -> AuditLog:
    global _DEFAULT_AUDIT
    if _DEFAULT_AUDIT is None:
        from .config import LOG_ROOT

        try:
            _DEFAULT_AUDIT = AuditLog(path=LOG_ROOT / "audit.jsonl")
        except OSError:
            _DEFAULT_AUDIT = AuditLog()
    return _DEFAULT_AUDIT


# --------------------------------------------------------------------------- #
# Operations router (mounted at /_ops by deploy/asgi.py)
# --------------------------------------------------------------------------- #


def _build_ops_router():
    from fastapi import APIRouter, Response

    router = APIRouter()

    @router.get("/healthz")
    def healthz() -> dict[str, Any]:
        return liveness()

    @router.get("/readyz")
    def readyz(response: Response) -> dict[str, Any]:
        status_code, body = readiness()
        response.status_code = status_code
        return body

    @router.get("/metrics")
    def metrics() -> Response:
        try:
            text = default_registry().render_prometheus()
            return Response(content=text, media_type="text/plain; version=0.0.4; charset=utf-8")
        except Exception:  # §7.6
            return Response(
                content=json.dumps({"code": "METRICS_RENDER_FAILED", "message": "metrics render failed"}),
                status_code=500,
                media_type="application/json",
            )

    return router


ops_router = _build_ops_router()


# --------------------------------------------------------------------------- #
# Request-context middleware (§8.1 position 1)
# --------------------------------------------------------------------------- #


class RequestContextMiddleware:
    """Outermost middleware: assign request/trace id, bind the context, emit one
    access-log record per request, and record request-count/latency metrics."""

    def __init__(self, app, *, registry: "MetricsRegistry | None" = None) -> None:
        self.app = app
        self.registry = registry or default_registry()
        self._logger = logging.getLogger("monsoon_grid.access")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from starlette.requests import Request

        request = Request(scope, receive=receive)
        trace_id, span_id, sampled = parse_trace_headers(request.headers)
        request_id = secrets.token_hex(16)
        context = RequestContext(
            request_id=request_id,
            trace_id=trace_id,
            span_id=span_id,
            trace_sampled=sampled,
            route=_templated_route(request.url.path),
            method=request.method,
            principal=None,
            tenant=None,
            started_monotonic=time.monotonic(),
        )
        token = set_context(context)
        try:
            scope.setdefault("state", {})
            request.state.request_id = request_id
        except Exception:
            pass

        captured: dict[str, int] = {"status": 0}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                captured["status"] = message["status"]
                headers = message.setdefault("headers", [])
                headers.append((b"x-request-id", request_id.encode("latin-1")))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            latency_ms = (time.monotonic() - context.started_monotonic) * 1000.0
            status = captured["status"] or 500
            self._record(context, status, latency_ms)
            reset_context(token)

    def _record(self, context: RequestContext, status: int, latency_ms: float) -> None:
        status_class = f"{status // 100}xx"
        counter = self.registry.get("monsoongrid_requests_total")
        if isinstance(counter, Counter):
            counter.inc(route=context.route, method=context.method, status=str(status), status_class=status_class)
        hist = self.registry.get("monsoongrid_request_latency_seconds")
        if isinstance(hist, Histogram):
            hist.observe(latency_ms / 1000.0, route=context.route, method=context.method)
        self._logger.info("request.completed", extra={"monsoon_fields": {}, "status": status, "latency_ms": round(latency_ms, 3)})


def _templated_route(path: str) -> str:
    for raw, label in (
        (r"/replays/[^/]+/advance", "/replays/{replay_id}/advance"),
        (r"/replays/[^/]+", "/replays/{replay_id}"),
        (r"/state/[^/]+", "/state/{replay_id}"),
        (r"/forecasts/[^/]+", "/forecasts/{replay_id}"),
        (r"/scenarios/[^/]+", "/scenarios/{replay_id}"),
        (r"/metrics/[^/]+", "/metrics/{replay_id}"),
        (r"/events/[^/]+", "/events/{replay_id}"),
        (r"/certificates/[^/]+", "/certificates/{certificate_id}"),
        (r"/ui/[^/]+", "/ui/{path}"),
    ):
        if re.fullmatch(raw, path):
            return label
    return path

