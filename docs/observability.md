# MonsoonGrid observability

Covers the structured logging, metrics, readiness, SLOs, and operational audit
delivered by `observability.py`. No SLO is claimed to be met.

## 1. Structured logging

One line of JSON on stdout per record, in Cloud Logging structured-payload shape.
Fields are governed by the `LOGGABLE_FIELDS` allowlist; any other caller-supplied
key is **dropped** (not masked). Example record:

```json
{"severity":"INFO","time":"2026-07-01T06:15:00.123456Z","message":"request.completed",
 "logging.googleapis.com/trace":"projects/<project>/traces/<32hex>",
 "logging.googleapis.com/spanId":"<16hex>","request_id":"<32hex>",
 "route":"/planning","method":"POST","status":200,"latency_ms":412.7,
 "principal":"oidc:1029384756","tenant":"t-pilot","replay_id":"...","decision_id":"...",
 "slot_index":42,"service":"monsoongrid","revision":"tree-<digest12>","scope":"REPLAY"}
```

Trace correlation precedence: W3C `traceparent`, then Google
`X-Cloud-Trace-Context`, then a freshly generated 32-hex trace id. The trace
resource field is emitted only when the GCP project id is known, because Cloud
Logging silently drops a malformed trace name.

Redaction (`redact_mapping`): allowlist first; the four generic numeric keys
(`count`, `seconds`, `bytes`, `attempt`) pass a numeric type check and are
otherwise redacted; a value-pattern backstop masks Bearer/JWT/PEM-shaped strings.
A bare 64-hex digest is **not** redacted — digests are evidence, not secrets. The
key names `seed`, `signing_key`, `private_key`, `token`, `authorization`,
`secret`, `password`, `api_key` are hard-blocked by name and absent from the
allowlist.

Uvicorn's own loggers use the same formatter via `deploy/uvicorn-logging.json`,
with `--no-access-log` so the only access record is ours (one request -> one
access record).

## 2. Metrics — and the log-based-metrics trade-off

A self-contained Prometheus-text registry renders `/_ops/metrics`. The instrument
catalogue is enforced by `install_metrics()` and asserted by name/type/labels in
`tests/test_observability.py` (AC-24).

Transport to Cloud Monitoring is **log-based metrics** (`deploy/monitoring/log-based-metrics.json`),
chosen over a GMP or OpenTelemetry Collector sidecar. The trade-off, stated
plainly: a sidecar means an extra container, extra memory, and extra cost; log-based
metrics need zero extra infrastructure and no API beyond Cloud Logging. The
downside is coarser histogram fidelity than native Prometheus histograms and
billing on log volume. If fidelity becomes the binding constraint, the GMP sidecar
is the documented upgrade and `/_ops/metrics` already exists for it.

Label cardinality is bounded: `route` uses the templated path
(`/replays/{replay_id}`), a label value over 64 chars or more than 2000 distinct
tuples per instrument is dropped and counted in
`monsoongrid_metric_cardinality_dropped_total`.

## 3. Health and readiness

- `GET /_ops/healthz` — liveness. Returns `200` unconditionally once serving;
  touches no database, filesystem, network, or `scipy` import. A liveness probe
  that depended on a dependency would turn an outage into a restart loop.
- `GET /_ops/readyz` — readiness, aggregating `database`, `trust`, `solver`,
  `audit_sink`. Any `unavailable` -> `503 unready`; any `degraded` and no
  `unavailable` -> `200 degraded`; all `ok` -> `200 ready`. A node whose MILP
  solver is missing but whose deterministic fallback is importable reports
  `degraded` and keeps serving, because it can still produce a validated,
  certificate-bearing decision.

The `trust` check is a five-branch decision tree: keyring invalid -> `unavailable`
(`trust_anchor_invalid`); valid keyring, no signing key -> `degraded`
(`verify_only_no_signing_key`); valid keyring, unusable signing key ->
`unavailable` (`signing_key_unusable`); all present and matching -> `ok`. No branch
ever includes the seed, the key path, or exception text in the response body.

## 4. SLOs and alerting

`slo_definitions()` returns four objectives, each with `attained=None` and
`measured=False` by construction, so no SLO can be reported as met.

| SLO | Target | Window |
|---|---|---|
| S1 Decision availability | >= 99.0% | rolling 28d |
| S2 Decision latency | p95 <= 60s, p99 <= 285s | rolling 7d |
| S3 Fallback-rate ceiling | <= 5% | rolling 7d |
| S4 Certificate verification success | 100% | every verification |

Alert policies (`deploy/monitoring/alert-policies.json`) ship with an **empty**
notification-channel list so importing them cannot page an unintended address.

## 5. Audit vs. decision certificate

| | Decision certificate | Operational audit record |
|---|---|---|
| Purpose | cryptographic evidence a decision came from specific inputs | operational evidence an actor did a thing |
| Produced by | `certificates.create_certificate()` | `observability.AuditLog.append()` |
| Integrity | Ed25519 signature over a canonical unsigned payload | SHA-256 hash chain, verifiable without a key, plus platform retention lock |
| Binds | content-addressed manifest, replay/decision id, status | authenticated principal, action, target, binding digest, request/trace id |
| Lifetime | permanent evidence artifact | operational record, 3-year retention |
| Who may read | `viewer`+ | `auditor`, `admin` |
| Failure mode | verification failure -> evidence not trustworthy -> page (S4) | chain break -> record may be altered -> page |

They are deliberately separate streams with separate keys and stores. An audit
record is never signed with the decision-signing key.

## 6. Exact planner/validator metric wiring (the follow-up phase)

Syntactically valid Python referencing only delivered symbols. Until `planner.py`
reports a real `elapsed_seconds` (today it hard-codes `0.0`), the caller measures
the whole `plan_next` call at the boundary and ignores
`PlanResult.solver_result.elapsed_seconds`:

```python
import time
from monsoon_grid.observability import record_solver_result, record_safety_decision

t0 = time.perf_counter()
result = plan_next(portfolio, path, events, replay_id=replay_id, slot_index=slot,
                   policy_mode=policy_mode)
measured_seconds = time.perf_counter() - t0

record_solver_result(
    policy_mode=policy_mode,
    solver_name=result.solver_result.solver_name,
    status=result.solver_result.status,
    seconds=measured_seconds,                       # measured, not the hard-coded 0.0
    fallback_reason=result.fallback_reason,
    latency_source="measured_at_boundary",
)

decision = validate_proposal(...)
record_safety_decision(
    status=decision.status,
    violations=[(v.code, v.severity) for v in decision.violations],
    critical_unserved_mwh=decision.critical_unserved_mwh,
    reserve_shortfall_mwh=decision.reserve_shortfall_mwh,
)
```

Once `planner.py` reports real per-stage values, the same functions are called
with `latency_source="reported_by_planner"` and per-stage data appears. The
`monsoongrid_planner_stage_latency_unreported_total` counter keeps a reported `0.0`
from reading as "the solver was instantaneous".
