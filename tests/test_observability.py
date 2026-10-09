"""Observability unit tests (design §7; AC-18, 20-30)."""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

import pytest

from monsoon_grid import observability as obs


# --------------------------------------------------------------------------- #
# AC-18 redaction
# --------------------------------------------------------------------------- #


def test_redact_drops_non_allowlisted_and_masks_secrets():
    digest = "a" * 64
    values = {
        "principal": "oidc:123",
        "state_digest": digest,
        "authorization": "Bearer abc.def.ghi",  # hard-blocked by name
        "surprise": "whatever",  # not in allowlist -> dropped
        "reason": "Bearer sometoken value",  # pattern backstop
        "message": "ey" + "a" * 10 + "." + "b" * 10 + ".sig",  # JWT shape
    }
    out = obs.redact_mapping(values)
    assert out["principal"] == "oidc:123"
    assert out["state_digest"] == digest  # 64-hex digest survives
    assert "authorization" not in out
    assert "surprise" not in out
    assert out["reason"] == obs.REDACTED
    assert out["message"] == obs.REDACTED


def test_redact_numeric_keys_type_check():
    for key in ("count", "seconds", "bytes", "attempt"):
        assert obs.redact_mapping({key: 42})[key] == 42
        assert obs.redact_mapping({key: 1.5})[key] == 1.5
        assert obs.redact_mapping({key: "secret-string"})[key] == obs.REDACTED


# --------------------------------------------------------------------------- #
# AC-20 / AC-21 structured logging & trace parsing
# --------------------------------------------------------------------------- #


def test_json_formatter_shape_and_no_credentials():
    formatter = obs.JsonLogFormatter(gcp_project_id="proj", service="monsoongrid", revision="tree-abc")
    ctx = obs.RequestContext(
        request_id="r" * 32,
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        span_id="00f067aa0ba902b7",
        trace_sampled=True,
        route="/planning",
        method="POST",
        principal="oidc:1029384756",
        tenant="t-pilot",
        started_monotonic=0.0,
        replay_id="replay-x",
        decision_id="replay-x:decision:42",
        slot_index=42,
    )
    token = obs.set_context(ctx)
    try:
        record = logging.LogRecord("monsoon_grid.api", logging.INFO, __file__, 1, "request.completed", None, None)
        record.status = 200
        record.latency_ms = 412.7
        line = formatter.format(record)
    finally:
        obs.reset_context(token)
    payload = json.loads(line)  # single-line JSON
    for key in ("severity", "message", "time", "request_id", "route", "method", "status", "latency_ms"):
        assert key in payload
    assert payload["logging.googleapis.com/trace"] == "projects/proj/traces/4bf92f3577b34da6a3ce929d0e0e4736"
    assert payload["logging.googleapis.com/spanId"] == "00f067aa0ba902b7"
    assert payload["replay_id"] == "replay-x"
    assert payload["decision_id"] == "replay-x:decision:42"
    assert payload["slot_index"] == 42
    lowered = line.lower()
    for forbidden in ("authorization", "bearer", "seed"):
        assert forbidden not in lowered
    assert "token" not in [k for k in payload if k != "token_subject" and k != "token_fingerprint"]


def test_trace_header_precedence():
    tp = {"traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01"}
    trace, span, sampled = obs.parse_trace_headers(tp)
    assert trace == "a" * 32
    assert span == "b" * 16
    assert sampled is True

    cloud = {"X-Cloud-Trace-Context": "c" * 32 + "/255;o=1"}
    trace, span, sampled = obs.parse_trace_headers(cloud)
    assert trace == "c" * 32
    assert span == f"{255:016x}"
    assert sampled is True

    trace, span, sampled = obs.parse_trace_headers({})
    assert len(trace) == 32 and all(ch in "0123456789abcdef" for ch in trace)
    assert span is None and sampled is False


# --------------------------------------------------------------------------- #
# AC-23 / AC-24 metrics
# --------------------------------------------------------------------------- #


def test_render_prometheus_format():
    reg = obs.MetricsRegistry()
    c = reg.counter("demo_total", "demo counter", ("x",))
    g = reg.gauge("demo_gauge", "demo gauge", ("x",))
    h = reg.histogram("demo_latency", "demo histogram", (0.1, 0.5, 1.0), ("x",))
    c.inc(x="a")
    g.set(3.0, x="a")
    h.observe(0.2, x="a")
    h.observe(0.7, x="a")
    text = reg.render_prometheus()
    assert "# HELP demo_total demo counter" in text
    assert "# TYPE demo_total counter" in text
    assert "demo_latency_bucket" in text
    assert "demo_latency_sum" in text
    assert "demo_latency_count" in text
    assert 'le="+Inf"' in text
    # buckets monotonically non-decreasing
    buckets = [ln for ln in text.splitlines() if ln.startswith("demo_latency_bucket")]
    counts = [int(ln.rsplit(" ", 1)[1]) for ln in buckets]
    assert counts == sorted(counts)
    assert counts[-1] == 2  # +Inf == total


_CATALOGUE = {
    "monsoongrid_requests_total": ("counter", ("route", "method", "status", "status_class")),
    "monsoongrid_request_latency_seconds": ("histogram", ("route", "method")),
    "monsoongrid_errors_total": ("counter", ("route", "code")),
    "monsoongrid_planner_stage_latency_seconds": ("histogram", ("policy_mode", "stage_name", "objective_name", "status", "latency_source")),
    "monsoongrid_planner_stage_latency_unreported_total": ("counter", ("policy_mode", "stage_name")),
    "monsoongrid_planner_solve_latency_seconds": ("histogram", ("policy_mode", "solver_name", "status")),
    "monsoongrid_planner_fallback_total": ("counter", ("policy_mode", "fallback_reason")),
    "monsoongrid_validator_rejections_total": ("counter", ("violation_code", "severity")),
    "monsoongrid_validator_decisions_total": ("counter", ("status",)),
    "monsoongrid_escalations_total": ("counter", ("reason",)),
    "monsoongrid_certificate_operations_total": ("counter", ("operation", "outcome", "failure_code")),
    "monsoongrid_data_quality_total": ("counter", ("signal_class", "code", "effect")),
    "monsoongrid_auth_failures_total": ("counter", ("reason",)),
    "monsoongrid_authz_denials_total": ("counter", ("route", "required_role")),
    "monsoongrid_approval_rejections_total": ("counter", ("code",)),
    "monsoongrid_approvals_total": ("counter", ("action", "separation_of_duty", "author_class")),
    "monsoongrid_rate_limited_total": ("counter", ("rate_class", "scope")),
    "monsoongrid_tenant_denials_total": ("counter", ("route",)),
    "monsoongrid_cadence_skips_total": ("counter", ("loop", "reason")),
    "monsoongrid_body_limit_rejections_total": ("counter", ("route",)),
    "monsoongrid_request_timeouts_total": ("counter", ("route",)),
    "monsoongrid_jwks_refresh_failures_total": ("counter", ("reason",)),
    "monsoongrid_jwks_keys": ("gauge", ()),
    "monsoongrid_readiness_state": ("gauge", ("component",)),
    "monsoongrid_audit_records_total": ("counter", ("event_type",)),
    "monsoongrid_audit_chain_verified": ("gauge", ()),
    "monsoongrid_build_info": ("gauge", ("revision", "source_tree_digest", "python_version", "scipy_version")),
    "monsoongrid_metric_cardinality_dropped_total": ("counter", ("metric",)),
}


def test_instrument_catalogue_present():
    reg = obs.install_metrics(obs.MetricsRegistry())
    for name, (kind, labels) in _CATALOGUE.items():
        inst = reg.get(name)
        assert inst is not None, f"missing instrument {name}"
        expected_type = {"counter": obs.Counter, "gauge": obs.Gauge, "histogram": obs.Histogram}[kind]
        assert isinstance(inst, expected_type), f"{name} wrong type"
        assert inst.labels == labels, f"{name} labels {inst.labels} != {labels}"


# --------------------------------------------------------------------------- #
# AC-25 planner stage recording
# --------------------------------------------------------------------------- #


def test_record_planner_stage_and_unreported_zero():
    obs.install_metrics(obs.MetricsRegistry())
    obs.record_planner_stage(
        policy_mode="balanced", stage_name="tier-3", objective_name="net_cost",
        seconds=0.42, status="OPTIMAL", latency_source="reported_by_planner",
    )
    hist = obs.default_registry().get("monsoongrid_planner_stage_latency_seconds")
    assert sum(hist._totals.values()) == 1
    unreported = obs.default_registry().get("monsoongrid_planner_stage_latency_unreported_total")
    assert sum(unreported._values.values()) == 0

    obs.record_planner_stage(
        policy_mode="balanced", stage_name="tier-3", objective_name="net_cost",
        seconds=0.0, status="OPTIMAL", latency_source="reported_by_planner",
    )
    assert sum(unreported._values.values()) == 1


# --------------------------------------------------------------------------- #
# AC-26 liveness independent of externals
# --------------------------------------------------------------------------- #


def test_liveness_depends_on_nothing(monkeypatch):
    body = obs.liveness()
    assert body["status"] == "ok"
    assert "uptime_seconds" in body


# --------------------------------------------------------------------------- #
# AC-27 readiness aggregation
# --------------------------------------------------------------------------- #


def test_readiness_states():
    obs.install_metrics(obs.MetricsRegistry())
    ok = [
        lambda: obs.ComponentHealth("database", "ok", "x", 0.0),
        lambda: obs.ComponentHealth("trust", "ok", "x", 0.0),
        lambda: obs.ComponentHealth("solver", "ok", "x", 0.0),
        lambda: obs.ComponentHealth("audit_sink", "ok", "x", 0.0),
    ]
    status, body = obs.readiness(checks=ok)
    assert status == 200 and body["status"] == "ready"

    degraded = list(ok)
    degraded[2] = lambda: obs.ComponentHealth("solver", "degraded", "milp_unavailable_fallback_usable", 0.0)
    status, body = obs.readiness(checks=degraded)
    assert status == 200 and body["status"] == "degraded" and body["solver"] == "unavailable"

    unready = list(ok)
    unready[0] = lambda: obs.ComponentHealth("database", "unavailable", "err", 0.0)
    status, body = obs.readiness(checks=unready)
    assert status == 503 and body["status"] == "unready"


# --------------------------------------------------------------------------- #
# AC-27a trust decision tree (five branches)
# --------------------------------------------------------------------------- #


def _write_trust_fixture(tmp_path: Path) -> tuple[Path, Path, str]:
    import base64

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    seed = bytes(range(32))
    priv = Ed25519PrivateKey.from_private_bytes(seed)
    pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    import hashlib

    fingerprint = hashlib.sha256(pub).hexdigest()
    keyring = {
        "schema_version": "1.0",
        "keys": [{
            "key_id": "demo-key-1",
            "public_key_base64": base64.b64encode(pub).decode("ascii"),
            "fingerprint": fingerprint,
            "valid_from": "2026-01-01T00:00:00Z",
            "valid_until": "2030-01-01T00:00:00Z",
            "revoked": False,
        }],
        "trust_anchor": "operator-pinned-demo-fixture",
    }
    keyring_path = tmp_path / "keyring.json"
    keyring_path.write_text(json.dumps(keyring), encoding="utf-8")
    seed_path = tmp_path / "seed.hex"
    seed_path.write_text(seed.hex() + "\n", encoding="ascii")
    return keyring_path, seed_path, fingerprint


def test_trust_tree_branches(tmp_path, monkeypatch):
    obs.install_metrics(obs.MetricsRegistry())
    keyring_path, seed_path, fingerprint = _write_trust_fixture(tmp_path)

    # (a) keyring unset -> unavailable trust_anchor_invalid
    monkeypatch.delenv("MONSOON_GRID_TRUST_KEYRING", raising=False)
    monkeypatch.delenv("MONSOON_GRID_SIGNING_KEY", raising=False)
    monkeypatch.delenv("MONSOON_GRID_TRUST_FINGERPRINT", raising=False)
    res = obs.check_trust()
    assert res.state == "unavailable" and res.detail == "trust_anchor_invalid"

    # (b) keyring present but fingerprint absent -> unavailable
    monkeypatch.setenv("MONSOON_GRID_TRUST_KEYRING", str(keyring_path))
    monkeypatch.setenv("MONSOON_GRID_TRUST_FINGERPRINT", "f" * 64)
    assert obs.check_trust().state == "unavailable"

    # (c) valid keyring, signing key unset -> degraded verify-only
    monkeypatch.setenv("MONSOON_GRID_TRUST_FINGERPRINT", fingerprint)
    monkeypatch.delenv("MONSOON_GRID_SIGNING_KEY", raising=False)
    res = obs.check_trust()
    assert res.state == "degraded" and res.detail == "verify_only_no_signing_key"

    # (d) valid keyring, signing key is a 16-byte hex file -> unavailable signing_key_unusable
    bad = tmp_path / "bad.hex"
    bad.write_text(bytes(range(16)).hex() + "\n", encoding="ascii")
    monkeypatch.setenv("MONSOON_GRID_SIGNING_KEY", str(bad))
    res = obs.check_trust()
    assert res.state == "unavailable" and res.detail == "signing_key_unusable"

    # (e) matching 32-byte seed -> ok
    monkeypatch.setenv("MONSOON_GRID_SIGNING_KEY", str(seed_path))
    res = obs.check_trust()
    assert res.state == "ok"

    for res in [obs.check_trust()]:
        assert seed_path.read_text().strip() not in res.detail


# --------------------------------------------------------------------------- #
# AC-28 audit hash chain
# --------------------------------------------------------------------------- #


def test_audit_chain_links_and_tamper_detection(tmp_path):
    obs.install_metrics(obs.MetricsRegistry())
    path = tmp_path / "audit.jsonl"
    log = obs.AuditLog(path=path)
    h1 = log.append(event_type="startup", outcome="ok", principal=None, tenant=None, request_id="r" * 32, trace_id=None)
    h2 = log.append(event_type="approval_recorded", outcome="ok", principal="oidc:1", tenant="t-pilot", request_id="s" * 32, trace_id=None, detail={"approval_binding_digest": "a" * 64})

    lines = path.read_text(encoding="utf-8").splitlines()
    rec0 = json.loads(lines[0])
    rec1 = json.loads(lines[1])
    assert rec0["prev_hash"] == obs.GENESIS_PREV_HASH
    assert rec0["entry_hash"] == h1
    assert rec1["prev_hash"] == h1
    assert rec1["entry_hash"] == h2

    assert log.verify()["ok"] is True

    # tamper: alter an earlier record
    tampered = lines[:]
    bad = json.loads(tampered[0])
    bad["outcome"] = "tampered"
    tampered[0] = json.dumps(bad, separators=(",", ":"), sort_keys=True)
    path.write_text("\n".join(tampered) + "\n", encoding="utf-8")
    result = obs.AuditLog(path=tmp_path / "unused.jsonl").verify(path=path)
    assert result["ok"] is False and result["first_broken_seq"] == 0


# --------------------------------------------------------------------------- #
# AC-29 audit event types & no credentials
# --------------------------------------------------------------------------- #


def test_audit_records_event_types(tmp_path):
    obs.install_metrics(obs.MetricsRegistry())
    path = tmp_path / "audit.jsonl"
    log = obs.AuditLog(path=path)
    required = [
        "auth_failure", "authz_denied", "approval_recorded", "override_recorded",
        "escalation_recorded", "config_changed", "tenant_scope_denied",
    ]
    for event in required:
        log.append(event_type=event, outcome="ok", principal="oidc:1", tenant="t-pilot", request_id="r" * 32, trace_id=None)
    text = path.read_text(encoding="utf-8")
    for event in required:
        assert f'"event_type":"{event}"' in text
    assert "Bearer" not in text


# --------------------------------------------------------------------------- #
# AC-30 SLO definitions
# --------------------------------------------------------------------------- #


def test_slo_definitions_unmeasured():
    slos = obs.slo_definitions()
    assert len(slos) == 4
    for slo in slos:
        assert slo.target is not None
        assert slo.window
        assert slo.numerator and slo.denominator and slo.rationale
        assert slo.attained is None
        assert slo.measured is False
