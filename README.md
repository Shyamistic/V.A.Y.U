# MonsoonGrid P4

MonsoonGrid is a local, simulation-only F3/D2 vertical slice: typed input -> per-slot quality gate -> deterministic correlated scenarios -> scenario-aware lexicographic MILP or explicitly labelled fallback -> independent safety validator -> separate human approval event -> software energy twin -> signed offline certificate. It has no live grid, plant, SCADA, relay, inverter, or market command path.

## Run from PowerShell

From `C:\Users\shyam.BATCONSOLE\Desktop\etgenai`, the normative wrappers set exactly the product `src;runtime/site-packages` `PYTHONPATH` and invoke the supplied executor:

```powershell
& .\monsoon_grid\scripts\setup.ps1 -Rebuild
& .\monsoon_grid\scripts\discover_vayu_assets.ps1 -Output .\monsoon_grid\runtime\provenance\protected_manifest_before.json
& .\monsoon_grid\scripts\test.ps1
& .\monsoon_grid\scripts\replay.ps1 -Seed 20260701 -Policy balanced
& .\monsoon_grid\scripts\run_demo.ps1
& .\monsoon_grid\scripts\verify_certificate.ps1 -CertificatePath .\monsoon_grid\runtime\artifacts\certificate.json
& .\monsoon_grid\scripts\discover_vayu_assets.ps1 -Output .\monsoon_grid\runtime\provenance\protected_manifest_after.json -CompareTo .\monsoon_grid\runtime\provenance\protected_manifest_before.json
```

No command installs into `.venv`; dependencies target only `monsoon_grid/runtime/site-packages`. If SciPy/HiGHS is unavailable or times out, the planner records `SOLVER_UNAVAILABLE`, `SOLVER_TIMEOUT`, or `SOLVER_ERROR` and uses the deterministic `fallback-rule-table-v1`; the fallback is never labelled optimal. Certificate-producing commands require an externally pinned keyring/fingerprint. The isolated demo wrapper uses the immutable product `trust/` fixture only for this synthetic replay; production use must set operator-controlled `MONSOON_GRID_TRUST_KEYRING`, `MONSOON_GRID_TRUST_FINGERPRINT`, and `MONSOON_GRID_SIGNING_KEY`.

The exact roster is five solar farms, three wind farms, two virtual BESSs, three industrial load classes, `grid-main`, and `tie-main`, replayed over 96 15-minute slots from `2026-07-01T00:00:00Z`. `energy_mwh` is canonical BESS state and `soc_fraction` is derived. The planner models all five scenario paths, energy balance, BESS transitions, DR, market predicates, grid/tie constraints, terminal/reserve bounds, and policy objective values. VAYU is disabled by default and can only provide hash-verified weather context; the bench is mock-only. All checked-in numbers are synthetic or scenario assumptions, including the versioned carbon factor fixture.

## API

## Decision Desk Build

The served UI is a Vite/React/TypeScript application with a lazily loaded Three.js
portfolio schematic and evidence charts. Build it before starting the service:

```powershell
npm --prefix .\monsoon_grid\src\monsoon_grid\ui ci
npm --prefix .\monsoon_grid\src\monsoon_grid\ui run build
& .\monsoon_grid\scripts\start.ps1 -Port 8017
```

Configure `MONSOON_GRID_DEV_BEARER_TOKEN` with an operator-controlled, disposable
local-development credential before starting. The UI accepts that credential in
memory only; it does not store it in local/session storage. The preview is at
`http://127.0.0.1:8017/ui/`. Production must use the existing OIDC configuration;
local-development auth is forbidden in production and `/ui` remains denied there
until a reviewed browser-auth/hosting design lands. Local serving now uses the
same `deploy.asgi:app` security/observability composition as deployment.

The eleven views expose actual replay assets, storage, baseline forecasts,
scenario paths, fixture events, decisions, economics/baseline comparisons,
safety, scientific artifacts, software-only bench cases and signed evidence.
The demo sequence navigates real events and rejected slots; it issues no commands.
Phase 2 forecasts are not yet in dispatch. Bench frames are emulated, not hardware.
The 3D geometry is schematic, not surveyed geography or validated network physics.

Regenerate the demo with `run_demo.ps1` when `/api/workspace` reports missing or
inconsistent evidence. Tests use a separate `runtime-tests/` store so they cannot
overwrite the signed demo's SQLite records. Normal verification remains scoped
to the demo runtime, and the gate harness uses its separate `runtime-gates/` root.

## API Routes

`start.ps1` serves `127.0.0.1:8000`. Typed routes include `/health`, `/ready`, `/replays`, `/replays/{id}/advance`, `/validate`, `/approvals`, `/replays/{id}`, `/certificates/{id}`, `/metrics/{id}`, `/events/{id}`, `/adapter-status`, and `/demo`. Validation acceptance does not apply an action. A separate approval request with actor, reason, current state digest, and matching proposal digest is required before an advance can apply; overrides cannot bypass fatal findings. Valid safety rejection is HTTP 200 with a rejected envelope; malformed contracts are 422, state/idempotency conflicts are 409, and unavailable dependencies without a fallback are 503. There is deliberately no live-control route.

`/api/session` and `/api/workspace` require authentication. Workspace decisions
are validated and returned as a sorted array even though the stored artifact uses
slot-keyed records. Approval actors come from verified identity; self-approval is
rejected, and replay reads/idempotency are scoped to the tenant/principal.

## Evidence

`runtime/verification-note.md` records the actual commands, elapsed time, pass/fail counts, dependency outcomes, and protected-input comparison. `runtime/provenance/protected_manifest_before.json` is the exact protected-input baseline; the after inventory is compared against it. `runtime/artifacts/` contains canonical replay summary, scenarios, per-slot quality reports, approval events, metrics, event trace, and signed certificates. `runtime/store/sha256/` retains every certificate input and `runtime/store/monsoon_grid.sqlite3` is checked for artifact/manifest consistency. Replay digests exclude runtime timestamps, process IDs, and signatures. The adapter state/provenance and the carbon-factor version/digest are recorded in metrics and certificates.
