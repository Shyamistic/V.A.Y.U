# MonsoonGrid verification note

## Current verification: 2026-10-09

This section supersedes the historical record below. Results were observed against
the current working tree, including pre-existing uncommitted changes. No commit,
dependency installation, cloud mutation, API wiring, or frontend rebuild was performed.

All Python commands used the supplied `.venv/Scripts/python.exe` with product
`src;runtime/site-packages` on `PYTHONPATH`; no packages were installed into `.venv`.

### Commands and observed results

- `& .\monsoon_grid\scripts\run_demo.ps1 -Seed 20260701 -Policy balanced -Deadline 285`
   passed and regenerated `runtime/artifacts/` and its backing evidence store.
   Internal elapsed time: 76.406268 seconds; wrapper: 78.751700 seconds.
   Replay: `replay-20260701-balanced`, 96 slots, `scenario-02`, adapter `DISABLED`.
   Planner fallbacks: 3; applied fallbacks: 2; unsafe rejections: 2; approval events: 94.
- `& .\monsoon_grid\scripts\verify_certificate.ps1 -CertificatePath .\monsoon_grid\runtime\artifacts\certificate.json`
   passed: `verified=true`, `offline_replay=true`, `persistence_consistent=true`,
   manifest count 17. Pinned fingerprint:
   `56475aa75463474c0285df5dbf2bcab73da651358839e9b77481b2eab107708c`.
   Unsigned certificate digest:
   `61e607ef5e75f45724f074adc96dc4051b101eed19493265fee783527130e9c2`.
- `& .\monsoon_grid\scripts\test.ps1` passed with one skip and no failures.
   A separate `pytest --collect-only -o addopts= -q monsoon_grid/tests` collected
   350 tests: 349 passed, 1 skipped in the full run. A Starlette/AnyIO
   deprecation warning was emitted.
- `& .\monsoon_grid\scripts\check_gates.ps1 -Seed 20260701 -Policy balanced`
   passed Ruff across `src` and `tests` and the gate harness. G1, G2, G6, G9,
   and G10: PASS/BLOCKING. G3: WARN/non-blocking, with one committed variable
   deviating across scenarios. Summary: `PASS (G3 is non-blocking)`.
   An earlier gate invocation was interrupted and is not counted as verification.
   Gate artifacts use the separate `runtime-gates/` root, not the demo root.

### Regenerated demo metrics

Values below were parsed from `runtime/artifacts/metrics.json`, not copied from
tests or the separate gate replay. Units and provenance are present in that artifact.

| Metric | Value | Unit |
| --- | ---: | --- |
| net_cost_usd | 17328.526106 | USD |
| critical_unserved_mwh | 17.502473 | MWh |
| noncritical_unserved_mwh | 15.833178 | MWh |
| dr_shed_mwh | 0.021267 | MWh |
| dr_shift_mwh | 0 | MWh |
| dr_rebound_mwh | 0 | MWh |
| dr_cost_usd | 3.82797 | USD |
| market_revenue_usd | 1324.48 | USD |
| nonmarket_export_valuation_usd | 0 | USD |

The obsolete negative net cost and zero critical-unserved values no longer describe
the refreshed demo artifact. These are synthetic simulation results, not measured
plant performance, realized market returns, or regulatory-verified economics.

### Boundaries and remaining work

The service is simulation/replay only, with no live control or market submission.
The G3 non-anticipativity gap remains unresolved. Auth, observability, and tenancy
integration in `api.py`, and a redesigned frontend, were not implemented here.
Docker/cloud deployment, browser validation, a fresh two-run determinism comparison,
and a fresh protected-input inventory comparison were not performed in this refresh.
Historical results below are not evidence that those checks passed on today's tree.

## Historical Verification (Superseded)

The following is the prior local verification record, preserved for historical
context only. Its test counts, replay results, and digests are not current. All
Python execution in that record used only:

`C:\Users\shyam.BATCONSOLE\Desktop\etgenai\.venv\Scripts\python.exe`

The product wrappers used the product source and product-owned dependency path. No package was installed into `.venv`, and no VAYU tree, checkpoint/archive, research report, egg-info tree, `archive`, or other protected input was written.

## Implemented review changes

- Published the exact evidence paths `runtime/verification-note.md` and `runtime/provenance/protected_manifest_before.json`, with `protected_manifest_after.json` for the final comparison.
- Replaced the solver probe with a scenario-indexed lexicographic MILP primary path. The model covers all five scenario paths, five solar farms, three wind farms, two BESSs, canonical `energy_mwh` transitions at `dt_hours=0.25`, energy balance, DR, market predicates, renewable availability, grid/tie limits and ramps, reserve/terminal bounds, and policy objective values. Solver failure/timeout is recorded as an explicit fallback; fallback is never labelled optimal.
- Evaluated the quality gate on all 96 replay slots. Slot 40 records stale `deferrable_aux` telemetry and uses `DATA_QUALITY_FALLBACK` with no optional DR for that load. Critical quality failures are fatal/escalated.
- Separated validator acceptance from application. Every applied demo slot has a deterministic `ApprovalEvent` containing actor, reason, proposal digest, and current state digest. The twin independently validates the proposal immediately before mutation; slot 41 remains escalated and unapplied. API overrides cannot approve fatal findings.
- Closed proposal/twin safety checks for nonnegative values, exact rosters, renewable availability, BESS exclusivity/rates/reserve/terminal state, grid/tie flow/ramp, market gates, DR conservation/rebound/quality, energy balance, and independent recheck.
- Enforced VAYU as disabled-by-default, strict weather-context-only, content-hash-gated local/allowlisted GET input. Adapter state and provenance are carried into replay artifacts/certificates and cannot supply authoritative power, demand, storage, price, line, action, or safety values.
- Strengthened certificates with an externally pinned trust fixture/configuration, key validity/revocation checks, complete content-addressed evidence, SQLite/artifact consistency, and offline manifest verification.
- Added deterministic record-ID recomputation on validated deterministic model updates and recorded the artifact-level two-run comparison in `runtime/provenance/determinism-comparison.json`.
- Moved carbon intensity to `fixtures/carbon_factor.json`, version `synthetic-carbon-factor-v1`, with provenance digest `a24d7b42a41f49ddd80efa294f0076955aad26ebb1caf1e12096ce4fe2775896`. Metrics and certificates include its version and digest.

## Commands run and results

1. **Protected baseline inventory**

   `& .\monsoon_grid\scripts\discover_vayu_assets.ps1 -Output .\monsoon_grid\runtime\provenance\protected_manifest_before.json`

   **PASS.** 15 protected paths were captured, including recursive file hashes, missing/type/symlink/error metadata, and VAYU commit metadata.

2. **Focused remediation tests**

   `$env:PYTHONPATH = 'C:\Users\shyam.BATCONSOLE\Desktop\etgenai\monsoon_grid\src;C:\Users\shyam.BATCONSOLE\Desktop\etgenai\monsoon_grid\runtime\site-packages'; & 'C:\Users\shyam.BATCONSOLE\Desktop\etgenai\.venv\Scripts\python.exe' -m pytest -q .\monsoon_grid\tests\test_remediations.py`

   **PASS.** 7 focused tests passed for scenario objective evidence, quality/DR gating, approval separation, roster/negative-action safety, VAYU hashing, certificate trust validity, record IDs, and carbon provenance.

3. **Full test wrapper**

   `& .\monsoon_grid\scripts\test.ps1`

   **PASS.** 26 tests passed, 0 failed. One existing Starlette/AnyIO deprecation warning was emitted.

4. **Compilation and lint**

   `python -m compileall -q monsoon_grid/src monsoon_grid/tests` using the supplied executor: **PASS**.

   `python -m ruff check monsoon_grid/src monsoon_grid/tests` using the supplied executor: **PASS**.

5. **Exact final demo wrapper**

   `& .\monsoon_grid\scripts\run_demo.ps1 -Seed 20260701 -Policy balanced -Deadline 285`

   **PASS.** The final measured internal replay elapsed time was **43.544902 seconds** and the PowerShell wrapper elapsed time was **44.454543 seconds**, below the 300-second external budget. It produced 96 slots, realized path `scenario-02`, `fallback_count=16`, `unsafe_rejections=5`, `approval_events=91`, and `adapter_state=DISABLED`. The unsafe slot 41 was not applied. These are measured replay/accounting outputs, not live-system performance claims.

6. **Final certificate verification**

   `& .\monsoon_grid\scripts\verify_certificate.ps1 -CertificatePath .\monsoon_grid\runtime\artifacts\certificate.json`

   **PASS.** Offline Ed25519 verification succeeded against the pinned fingerprint `56475aa75463474c0285df5dbf2bcab73da651358839e9b77481b2eab107708c`; manifest count was 17, SQLite/artifact consistency was true, and offline replay evidence was true. Final unsigned digest: `b1c0187ccbecedc452d7a9cb4468128ad687f6194e6c09356d34cc3c9d2305d6`.

7. **Independent wrapper determinism**

   Two independent runs were executed with the same seed/policy into `runtime/determinism-a` and `runtime/determinism-b` using `run_demo.ps1 -RuntimeRoot ...`. Their wrapper elapsed times were **38.806251 seconds** and **38.384797 seconds**. Canonical bytes matched for scenarios, realized path, all slot states, metrics, JSONL trace, and unsigned certificate. Both unsigned certificate digests were `b9df2ee8763594bbaab80bb9c176b8fe3c36c4758c15b99751e3da18695a3083`. Only documented runtime envelope fields (wall elapsed metadata and signatures) were excluded.

8. **Protected after-inventory comparison**

   `& .\monsoon_grid\scripts\discover_vayu_assets.ps1 -Output .\monsoon_grid\runtime\provenance\protected_manifest_after.json -CompareTo .\monsoon_grid\runtime\provenance\protected_manifest_before.json`

   **PASS.** 15 paths compared; `match=True`; differences were `[]`.

## Limitations and claims boundary

This is a local deterministic simulation/replay and software energy twin only. It does not control a live grid, plant, SCADA/EMS, relay, inverter, physical process, market account, or physical endpoint. The checked-in portfolio, forecast, weather context, prices, carbon factor, and event paths are synthetic/scenario assumptions. The VAYU adapter remained disabled for the authoritative demo; its weather-context contract is not a calibrated weather-skill claim. No PHIL/HIL/CHIL, live-control, regulatory, or financial-performance claim is made. No real browser/DOM runner was used; the static/UI contract remains local-service-only. The optional bench remains MOCK-ONLY.
