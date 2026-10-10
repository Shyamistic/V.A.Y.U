# MonsoonGrid deployment

A reviewable deployment path plus a locally verifiable container image. This is
**not** a deployment: nothing is created in Google Cloud, no image is pushed, and
no billable API is enabled by this task. No statement here asserts that the system
is live, that any SLO is attained, or that a deployment completed.

## Frontend Expansion Update (2026-10-09)

The Dockerfile now adds a Node UI build stage using the checked-in npm lockfile
and copies only the resulting `dist/` into the runtime. `.dockerignore` excludes
local dependencies, runtime stores, credentials and data caches. Recorded
`experiments/results/` artifacts are copied for the scientific evidence view and
included in the source digest. Cloud Build now typechecks/builds and audits the
frontend before its existing backend/image gates.

This container change is prepared, not Docker-verified: the CLI is installed but
the Linux engine named pipe remains absent on the workstation. No image was built
or pushed. Production `/ui` denial is intentionally preserved; a public judging
site still needs reviewed browser authentication/static hosting. Replay artifacts
and approvals are not durable across Cloud Run instance replacement until the
storage adapters land. See `copilot-research.md` for the cost-controlled demo,
pilot, data/AI and optional hardware tiers and the credit-expiry checklist.

The existing multi-instance YAML is a future plan, not an approved deployment of
the current process-local replay state. Keep schedulers paused. A confirmed
project ID, region, IAM principals, budget, lifecycle/teardown plan and resolved
storage/auth gates are required before any actual provisioning or push.

## 1. Two requirements manifests (mandatory, not stylistic)

`requirements.lock` is a hash-pinned **Windows** lock: its single `--hash` per
distribution is the `win_amd64` wheel for `numpy`/`scipy`/`cryptography`/`cffi`/
`ruff`. `pip install --require-hashes` on Linux resolves a `manylinux` wheel whose
hash is not in the file and fails. That is why `deploy/requirements-linux.txt`
exists: platform-independent `==` pins, runtime-only. `deploy/requirements-linux-dev.txt`
adds the CI-only test tooling. `deploy/check_requirements_sync.py` is the drift
gate and runs as the first Cloud Build step.

Three deliberate differences from the Windows lock: `colorama` omitted (Windows-only
`click` dependency); dev tooling moved to the dev manifest; `anyio` pinned to
`4.15.1` exactly because that version dropped the `sniffio` requirement.

### Linux hash generation (R-1)

Linux wheel hashes cannot be generated from this host (no PyPI network). Inside the
build container:

```
pip download --dest /tmp/wheels --no-binary :none: -r deploy/requirements-linux.txt
pip hash /tmp/wheels/* > deploy/requirements-linux.hashes
```

Commit the result and switch the Dockerfile to `--require-hashes`. Until then the
image installs from unhashed pins (residual risk R-1).

## 2. Container

Two-stage Dockerfile on `python:3.13-slim-bookworm`, non-root UID 10001, no build
toolchain in the final layer, a `HEALTHCHECK` on `/_ops/healthz`, and
`SOURCE_REVISION`/`SOURCE_TREE_DIGEST` build args. The base tag is pinned by
**digest** on the first successful build (residual risk R-16 until then).

Read-only root filesystem: a `trust/` directory whose content already equals what
`certificates.ensure_demo_trust()` would write is baked into the image, so every
write branch is a no-op and `/ready` works under `--read-only`. The only writable
surface is `MONSOON_GRID_RUNTIME_ROOT=/var/monsoon/runtime` and `/tmp`.
`src/monsoon_grid/ui/` is deliberately baked in so the production `/ui` denial
comes from the route policy (`403`), not from the assets being absent (`404`).

The container serves `deploy.asgi:app`, not `monsoon_grid.api:app`, which is what
makes it secure without editing `api.py`.

### CPU throttling

`run.googleapis.com/cpu-throttling: "true"` because `src/monsoon_grid/` has no
in-process background loop (verified: no `threading`/`asyncio.create_task`/
`BackgroundTasks`/`on_event`/`lifespan`), so CPU outside a request does nothing but
cost money. **If** a later phase adds an in-process dispatch loop, set
`cpu-throttling: "false"` and re-price, because a throttled instance will not run a
background timer.

## 3. Service inventory (each justified)

Artifact Registry (deployable image + scanning); Cloud Run (stateless HTTP,
scale-to-zero-capable, native OIDC invocation); Cloud SQL Postgres (durable state
fixing S2/S3); Cloud Storage (content-addressed evidence + bucket-locked audit
archive); BigQuery (3-year 15-min metrics ledger); Secret Manager (signing seed,
fingerprint, role map, DB password); Cloud Scheduler (the two cadences);
Cloud Logging/Monitoring (telemetry); three IAM service accounts (disjoint
least-privilege roles).

### Considered and excluded

- **Pub/Sub** — excluded. Scheduler invokes Cloud Run directly with OIDC; one
  consumer, no fan-out, no replay-from-offset. Revisit when a second independent
  consumer of dispatch events exists.
- **GPUs** — excluded. The planner is a CPU-only lexicographic MILP via
  `scipy.optimize.milp`; no training, no inference, no dense linear algebra at GPU
  scale.
- **Firebase** — excluded. The UI is static files already served by the app; no
  mobile client, realtime sync, push, or Firebase Auth need. IAP in front of Cloud
  Run is the cheaper fit if a browser session layer becomes necessary.
- **Memorystore / Redis** — excluded for the pilot. Its purpose (shared rate-limit
  bucket, shared idempotency cache) is covered by per-instance buckets at
  `maxScale: 3` and by moving idempotency to Postgres; its fixed monthly cost has no
  scale-to-zero.
- **Cloud Load Balancing + Cloud Armor** — excluded for the pilot. Cloud Run's
  `run.invoker` IAM check plus OIDC verification is the boundary; a WAF matters once
  a public browser surface exists (the default-denied `/ui` avoids that).
- **VPC Service Controls / Private Service Connect** — excluded for the pilot
  (residual risk R-8); right for a production tenant, out of proportion for a
  prototype.
- **Vertex AI / Gemini** — excluded entirely from the decision path; introducing a
  token-billed model would break determinism and the certificate story.
- **Cloud Tasks** — excluded. Scheduler's built-in retry covers the two cadences.
- **Cloud KMS** — considered, deferred (residual risk R-4). `certificates._private_key()`
  reads a file path and builds the key locally; KMS requires editing
  `certificates.py`, which this task may not modify.

## 4. Scheduler cadence

Both jobs (`deploy/scheduler-jobs.yaml`) are created **PAUSED** with OIDC
service-account auth and the service URL as audience. Cloud Scheduler sends a
static body and static headers with no runtime substitution, so a static
idempotency key would make the fast loop replay its first response forever;
`deploy/cadence.py` derives the per-slot key server-side. The fast-loop job must
stay paused until the Postgres adapter lands, because it reads the process-local
`api.REPLAYS` and is correct on one instance but wrong on several (residual risk
R-19). Cloud Scheduler has no native dead-letter queue for HTTP targets; after
retries exhaust, the failure is logged, the `decision-slot-missed` alert fires, and
the slot is a gap in the S1 numerator. A true dead-letter needs the Pub/Sub
topology that §3 excluded.

## 5. Database migration

`deploy/sql/001_init.sql` creates the five tables (`tenants` plus the four mapped
from SQLite), each with a `NOT NULL tenant_id` FK, tenant-leading composite PKs, a
unique index on `approval_binding_digest`, the `approvals_no_self_approval` CHECK,
and state-enum CHECKs. `003_seed_tenant.sql` inserts `t-pilot`. `002_rollback.sql`
drops in reverse FK order and is destructive. Execution requires a Postgres
instance and is out of scope here (validated by SQL review).

**Rollback procedure.** Take a Cloud SQL on-demand backup; apply
`002_rollback.sql`; redeploy the previous revision which still speaks SQLite;
restore from the backup if the application has already written Postgres-only rows.

**Blocking prerequisite:** `psycopg` is not in `requirements.lock`, which this task
may not modify, so the `PostgresPersistenceStore` adapter is delivered as a
documented contract (mirroring `PersistenceStore`'s public surface with a leading
`tenant_id`), not as code (residual risk R-3).

## 6. Provisioning

`deploy/provision.ps1` and `deploy/teardown.ps1` default to dry-run, route every
mutating `gcloud` call through a single `Invoke-Planned` gate, print a cost-first
per-resource plan with a total, and refuse to act without `-Confirm`. The project
guard refuses an empty id, refuses a mismatch against the active project without
`-Force`, and **unconditionally refuses `fablecraft-4ab6c`** even with `-Force`.
Teardown additionally requires `-IUnderstandDataLoss` for Cloud SQL and
retention-locked buckets. These are a deliberate manual operator action, never
automated. The captured dry-run plan is in the task artifacts
(`provision-plan.txt`); it performed no mutation and exited 0.

## 7. Verification results

### C2 local container build (item 10)

The Docker CLI (28.5.2) is installed but the `desktop-linux` engine named pipe is
absent — Docker Desktop is not running. `deploy/verify_image.ps1` detected exactly
that and exited 2 without fabricating any measurement:

```
status: DOCKER_DAEMON_UNAVAILABLE
detail: error during connect: Get "http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine/v1.51/version": open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.
remediation: Start Docker Desktop and wait for the Linux engine, then re-run deploy/verify_image.ps1.
DOCKER_DAEMON_UNAVAILABLE
exit code: 2
```

The image was therefore NOT built on this machine; the Dockerfile is unverified.
No image size, startup time, HiGHS-in-container result, or 401-rejection result is
reported because none was measured. Re-run `deploy/verify_image.ps1` once Docker
Desktop's Linux engine is running to produce the real numbers.

### Regression (items 0, 8, 17)

The design named a 38-test / 17-file baseline. The repository has since grown under
concurrent workflows; the measured pre-task collect-only count is **279**. The
regression contract honoured here is: this task's owned changes break no existing
test, and the owned tests pass.

- `pytest --collect-only -q tests` before owned test files: 279.
- `pytest -q tests` after this task: 312 passed, 1 failed. The one failure is
  `tests/test_report_only_coupling.py::test_planner_does_not_reference_dsm_or_obligation`,
  which reads `src/monsoon_grid/planner.py` and asserts it contains no "obligation"
  substring. That file and that test are owned by a concurrent workflow, not by this
  task; this task never touched `planner.py` or that test. The failure is pre-existing
  relative to this task's owned surface.
- Owned contribution: `tests/test_auth.py` (27 cases) and `tests/test_observability.py`
  (13 cases) all pass.
- `ruff check --no-cache` on the owned files adds no finding.

### Post-deployment verification checklist (NOT executed)

Marked not executed; run after a real deploy:

- `gcloud run services describe monsoongrid` shows the SOURCE_REVISION label.
- `curl $URL/_ops/healthz` returns 200; `/_ops/readyz` returns `trust: ok`.
- An unauthenticated `POST /approvals` returns 401.
- Cloud SQL reachable via the connector; `001_init.sql` applied; `t-pilot` seeded.
- Secret Manager mounts the signing seed at the configured path.
- Both Scheduler jobs exist and are PAUSED.
- The vulnerability scan step passed at the configured severity threshold.

## 8. Residual risk (R-1..R-19)

| # | Risk | Why it remains | Unacceptable when |
|---|---|---|---|
| R-1 | Linux image installs from unhashed pins | Linux wheel hashes need PyPI network | before any real-decision deployment |
| R-2 | Separation-of-duty unverifiable after restart / across instances; prod fails approvals closed | `DecisionAuthorIndex` is process-local (S2) | immediately in any multi-instance deployment |
| R-3 | Durable state still process-local (S2/S3 open in code) | `api.py`/`persistence.py` not modifiable; `psycopg` not in the lock | before a second concurrent operator |
| R-4 | Signing key is raw seed bytes, not an HSM | `certificates._private_key()` reads a file path | when certificates become externally relied-upon |
| R-5 | Rotation is a hard fingerprint switch, no dual-accept window | `_trusted_key()` requires exactly one match | when rotation must avoid a verification gap |
| R-6 | Tenant isolation is app-enforced, no RLS | one role + one tenant make RLS surface without gain | the day a second tenant exists |
| R-7 | No human browser path; `/ui` default-denied in production | a browser cannot attach a bearer token to assets | when an operator needs the UI; IAP is the fix |
| R-8 | No network perimeter (no VPC-SC / Cloud Armor) | out of proportion for a prototype | when holding a real tenant's data |
| R-9 | Per-instance rate limits (effective <= maxScale x configured) | shared store excluded on cost | when maxScale grows or a cost attack is credible |
| R-10 | Timeout bounds client latency, not the running sync thread | every route is a sync def in a threadpool | if a handler can block indefinitely |
| R-11 | Audit chain is per-instance, tamper-evident not tamper-proof | a global chain needs a single writer | when records must be legally defensible |
| R-12 | Hand-rolled JWT verification | no JWT library in the frozen set | if the seven negative tests are judged insufficient |
| R-13 | 2 vCPU / 2 GiB sizing is reasoned, not measured | no containerized 96-slot replay profiled | before capacity is promised |
| R-14 | No secret scanning / CVE pre-commit gate / SBOM | the scan exists only as a Cloud Build step | before the first real push |
| R-15 | No DDoS/load/pen testing, no third-party review | out of scope | before any production exposure |
| R-16 | Base image digest unpinned until first build | needs a working daemon or registry | at the first build |
| R-17 | Untyped `/api/*` routes accept `dict[str, Any]`, unbounded within the 1 MiB limit | adding a model edits `api.py`/`contracts.py` | when `/api/*` is exposed beyond the pilot operator |
| R-18 | No two-person rule for scheduler-authored decisions | needs a second approver on the contract | when an approval is externally relied-upon review |
| R-19 | `deploy/cadence.py` resolves the active replay from process-local `api.REPLAYS` | S2 unfixed; `api.py` not modifiable | the moment maxScale > 1 and the fast-loop job is resumed |
