# MonsoonGrid security

Scope labels: `SIMULATION` / `REPLAY` / `NO LIVE CONTROL` / `NO MARKET SUBMISSION`.
This document covers the authentication, authorization, approval-integrity,
tenancy, and secret-management posture delivered by `auth.py`, `tenancy.py`, and
`observability.py`, plus the exact `api.py` wiring the follow-up phase applies.

Nothing here claims the system is deployed, that any SLO is met, or that the
container was built on this machine.

## 1. Threat model

The highest-severity threat is a **forged dispatch approval**: manufacturing the
human-in-the-loop evidence the safety argument rests on.

| # | Actor | Motivation | Attack | Severity | Control |
|---|---|---|---|---|---|
| T1 | External unauthenticated attacker | manufacture operator consent | `POST /approvals` with a chosen `actor_id` | **highest** | Cloud Run IAM invoker + OIDC verification + `approver` role + `authorize_approval()` deriving `actor_id` from the verified subject + eight-input binding digest + a unique DB index on that digest |
| T2 | Authenticated `operator` | push their own proposal without a second person | approve own decision | high | Only `operator` can author a decision; `RoleMap.validate_for("production")` forbids `operator`+`approver`; per-decision self-approval check against `DecisionAuthorIndex`; `approvals_no_self_approval` DB CHECK |
| T2a | Authenticated `approver` | rubber-stamp a cadence proposal they commissioned | approve a scheduler-authored decision | medium | Not prevented, said plainly: the self-approval check is satisfied by construction on this path. Attribution via `author_class="service_account"` on the audit record and `approvals_total`. Residual risk R-18 |
| T3 | External attacker | resource exhaustion / cost | flood `/demo` or `/planning` | high | `mutating_heavy` bucket (3 burst, 0.2/s), `containerConcurrency: 4`, `maxScale: 3`, `timeoutSeconds: 300`, body limits |
| T4 | External attacker | forge decision evidence with the checked-in demo seed | sign a certificate with `bytes(range(32))` | high | the demo seed is public by construction and must never be the production anchor; production verification requires an operator-pinned fingerprint from Secret Manager (§3) |
| T5 | Insider with repo read access | extract production secrets | read `trust/demo-signing-seed.hex` | medium (demo value) | documented demo-only; production seed exists only in Secret Manager |
| T6 | Compromised service instance | erase evidence | delete audit objects / rewrite the chain | high | `objectCreator` without `objectViewer`/`objectAdmin` on the audit bucket; bucket-lock retention; audit head shipped to Cloud Logging within seconds |
| T7 | Attacker with a token for tenant B | read tenant A's data | request tenant A resources | high (future) | tenant derived from the verified principal only; tenant-leading composite keys; adapter-level predicate; `404` on mismatch |
| T8 | Attacker controlling an upstream proxy header | pick their own rate-limit bucket | spoof `X-Forwarded-For` | medium | last-entry parsing; `MONSOON_GRID_TRUST_FORWARDED_FOR` is explicit configuration |
| T9 | Attacker with an expired token | reuse credentials | replay | medium | `exp` with 60 s skew; `token_fingerprint` in the audit stream makes reuse detectable |
| T10 | Attacker exploiting hand-rolled JWT verification | bypass authentication | `alg: none`, `alg: HS256`, missing `kid` | high | single-element `RS256` allowlist, mandatory `kid`, signature verified before any claim is read (each a named unit test, AC-3) |
| T11 | Curious reader of logs/errors | harvest credentials | read a token from a record | medium | allowlist redaction, `Principal` never stores the raw token, fixed `500` body with no exception text |
| T12 | Anyone | mistake a simulation output for a live-control instruction | misread the UI/API | product-level high | no live-control and no market-submission path exists; every envelope carries `replay_scope`; §5 states the boundary |

## 2. Trust boundaries

Five boundaries, each with what validates the crossing:

1. internet -> Cloud Run: IAM invoker check; TLS terminated by the platform.
2. Cloud Run -> application: OIDC verification, role check, tenant scope, body limit, rate limit.
3. application -> Cloud SQL: private connector, SA credential, tenant-predicated adapter queries.
4. application -> Cloud Storage: SA credential, digest verification on read, create-only on the audit bucket.
5. application -> Google JWKS: the only outbound internet dependency; HTTPS, size-capped, timeout-capped, cached.

Two boundaries **do not exist and must not be created**: application -> SCADA/plant
control, and application -> market-exchange submission.

### The forwarded-header asymmetry (deliberate)

`X-Forwarded-Proto` is read unconditionally by `is_https()`, because a spoofed
`https` can only cause HSTS to be *emitted* (fail-safe). `X-Forwarded-For` is
gated on `MONSOON_GRID_TRUST_FORWARDED_FOR`, because a spoofed value would let a
caller choose its own rate-limit bucket (fail-open). The asymmetry is intentional.

### Per-instance rate-limit caveat

The in-memory token-bucket store is per-instance. With `maxScale: 3` the effective
global limit is up to 3x the configured per-instance limit. The documented limits
are per-instance limits (residual risk R-9).

## 3. Secret management and the demo-seed migration

`trust/demo-signing-seed.hex` is `bytes(range(32)).hex()` by construction in
`certificates.ensure_demo_trust()` — **public by construction**, a test vector, not
a leaked secret. It must never sign anything an operator relies on. Migration:

1. Generate a real 32-byte CSPRNG seed off-host; never write it to the repo or a terminal transcript.
2. `gcloud secrets create monsoongrid-signing-seed --replication-policy=user-managed --locations=<region>` and add the seed as version 1 from a file, deleting the file. (Planned via `Invoke-Planned`, never executed here.)
3. Derive the Ed25519 public key, compute the SHA-256 fingerprint, publish an operator keyring JSON with `valid_from`/`valid_until`/`revoked:false`; store it and the fingerprint as `monsoongrid-trust-keyring` and `monsoongrid-trust-fingerprint`.
4. Mount the seed as a **secret volume** at `/etc/monsoon/secrets/signing-seed.hex` and set `MONSOON_GRID_SIGNING_KEY` to that path (`certificates._private_key()` reads a path, not a value).
5. Verify with `python -m monsoon_grid verify-certificate` and confirm `/_ops/readyz` reports `trust: ok`.
6. Keep `trust/demo-signing-seed.hex` unchanged and clearly demo-labelled; `tests/test_certificates.py` depends on it and this task may not modify either.

Rotation: add a new Secret Manager version; add the new public key to the keyring
with `valid_from = now` while the old key keeps its `valid_until`; update the
fingerprint and deploy; after the old `valid_until` passes, set `revoked: true`.
Note the constraint: `certificates._trusted_key()` requires **exactly one** keyring
entry matching the pinned fingerprint, so rotation is a deploy-time fingerprint
switch, not a dual-accept window (residual risk R-5).

Secret inventory: four secrets (`monsoongrid-signing-seed`,
`monsoongrid-trust-fingerprint`, `monsoongrid-role-map`, `monsoongrid-db-password`),
each scoped to `monsoongrid-run` only.

## 4. Data classification and retention

| Class | Examples | Store | Retention | Access |
|---|---|---|---|---|
| Public / demo | demo seed, fixtures, verification keyring | repository | indefinite | anyone |
| Operational evidence | certificates, content-addressed objects | GCS evidence bucket, Cloud SQL | 3 years | `viewer`+ |
| Operational audit | the audit stream | GCS audit bucket (bucket-locked), Cloud Logging | 3 years, immutable | `auditor`, `admin` |
| Identity / PII | principal email addresses | audit stream only | 3 years, same lock | `auditor`, `admin` |
| Secret | signing seed, DB password, role map, dev token | Secret Manager | until rotated | `monsoongrid-run` only |
| Never stored | raw bearer tokens | nowhere — only sha256 fingerprints | n/a | n/a |

Email addresses are the only PII and appear only in the audit stream, never in a
certificate, log record, or response body.

## 5. The no-live-control / no-market-submission boundary

MonsoonGrid has no SCADA, OPC-UA, Modbus, DNP3, or market-exchange client, and no
outbound write path to any physical or commercial system. The only outbound
network dependencies are Google JWKS and the Google Cloud service APIs the runtime
SA is bound to. `GET /health` returns the label list exactly as it appears in
`api.py`:

```python
["SIMULATION", "REPLAY", "NO LIVE CONTROL / NO MARKET SUBMISSION"]
```

Adding such a path is a product-level decision requiring a separate safety case,
not a feature increment. The `approver` role, the validator, and the certificate
chain are *advisory* evidence for a human operator who acts through their existing
control system.

## 6. Exact `api.py` wiring (the follow-up patch)

This block is syntactically valid Python referencing only symbols delivered in
`auth.py`/`observability.py`. The composition root `deploy/asgi.py` already
installs the middleware today; this per-route patch is the stronger end state
(defense in depth — do NOT remove the middleware when adding it).

```python
# 1. after the existing imports in api.py
from .auth import Principal, authorize_approval, require_roles

# 2. POST /replays  -- signature gains one parameter
def create_replay(request: ReplayCreateRequest,
                  idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                  principal: Principal = Depends(require_roles("operator"))):
    ...

# 3. advance / validate / planning  -- same parameter, operator-only
#    def advance_replay(..., principal: Principal = Depends(require_roles("operator"))):
#    def validate(...,       principal: Principal = Depends(require_roles("operator"))):
#    def planning(...,       principal: Principal = Depends(require_roles("operator"))):

# 4. POST /demo  -- operator only; admin deliberately NOT granted (MEDIUM-4)
def demo(request: DemoRequest,
         idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
         principal: Principal = Depends(require_roles("operator"))):
    ...

# 5. the three untyped /api/* mutating routes -- named individually
def deliberate_slot(body: dict,
                    principal: Principal = Depends(require_roles("operator"))): ...
def parse_multimodal_document(body: dict,
                              principal: Principal = Depends(require_roles("operator"))): ...
def parse_multimodal_nowcast(body: dict,
                             principal: Principal = Depends(require_roles("operator"))): ...

# 6. POST /approvals  -- the forged-approval fix
def approval(request: ApprovalRequest,
             idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
             principal: Principal = Depends(require_roles("approver"))):
    authorization = authorize_approval(
        principal=principal,
        replay_id=request.replay_id,
        decision_id=request.decision_id,
        expected_state_digest=request.expected_state_digest,
        observed_state_digest=_state_digest(record),
        idempotency_key=request.idempotency_key,
        action=request.action,
        reason=request.reason or "",
        author=DECISION_AUTHORS.author_of(replay_id=request.replay_id,
                                          decision_id=request.decision_id),
        now=dt.datetime.now(dt.timezone.utc),
        production=(config.env == "production"),
    )
    # ApprovalRejected -> 409 if retryable else 403; otherwise:
    approval_event = ApprovalEvent(..., actor_id=authorization.actor_id, ...)
```

Read routes gain `Depends(require_roles("viewer"))`; `/health` and `/ready` gain
nothing.

### Policy <-> dependency checklist

`deploy/check_route_coverage.py` keeps the `ROUTE_POLICY` table and the real route
set in correspondence mechanically. Run it (from the repo root) as:

```
python -m deploy.check_route_coverage
```

Today it reports exactly the nine mutating/compute routes the patch above must add
(`/replays`, `/replays/{id}/advance`, `/validate`, `/planning`, `/demo`,
`/approvals`, `/api/deliberate`, `/api/multimodal/document`,
`/api/multimodal/nowcast`) and zero unmatched routes.

## 7. What the later phase MUST NOT do

- Do not remove the middleware when adding the dependencies (both layers are wanted).
- Do not read `request.actor_id` anywhere; the actor is `authorization.actor_id`.
- Do not log `principal.email` outside the audit stream.
- Do not add a route without a `ROUTE_POLICY` entry; the default-deny branch blocks it and logs `route_policy_unmatched`, which is the intended failure.

## 8. Residual risk

See `docs/deployment.md` §Residual risk for the full R-1..R-19 list with owners and
the condition under which each becomes unacceptable. The security-relevant ones:
R-1 (unhashed Linux pins), R-2 (process-local author index fails approvals closed
across instances), R-4 (raw-seed signing key, not KMS), R-5 (hard fingerprint
rotation), R-6 (app-enforced tenancy, no RLS), R-9 (per-instance rate limits),
R-11 (per-instance audit chain), R-12 (hand-rolled JWT), R-17 (unschema'd `/api/*`),
R-18 (no two-person rule for scheduler-authored decisions).
