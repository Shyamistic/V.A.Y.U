# MonsoonGrid Phase 2 — Probabilistic Forecasting & Calibration

This document describes the `src/monsoon_grid/forecasting/` package and its experiment harness.
It is a **read-only** subsystem: it does not wire itself into the dispatch engine. A later phase
adopts it through `forecasting/interface.py` (see "Integration" below). The authoritative design
is `.agents/pipeline/forecasting-phase2/forecasting-design.md`; this doc summarizes it and records
what is and is NOT validated.

## The four-stage chain

```
as-issued NWP forecast (issue_time, valid_time)
  -> [A] physical available-power baseline  (PV chain / wind chain), available only where a
         dataset supplies site geometry
  -> [B1] residual quantile model  q_tau(r | features),  r = (y - baseline) / C_ac
  -> [B2] distribution-free calibration (EnbPI-style ensemble + Adaptive Conformal Inference),
          per horizon x regime, reporting coverage AND sharpness
  -> [B3] joint scenarios with dependence ESTIMATED from data (block bootstrap primary,
          Gaussian copula comparator), probability-weighted, reduced by fast-forward selection
```

Four hard separations are structural: available vs delivered power; decision-time vs
verification information (`issue_time` vs `valid_time`); baseline source vs skill claim;
evaluation proxy vs production calculator (the DSM proxy is evaluation-only).

## Modules

| Module | Role |
| --- | --- |
| `errors.py` | 24-class error taxonomy, each with a stable `code`/`retryable`/`recoverable`. |
| `types.py` | Strict Pydantic base (`extra=forbid`, finite floats) and value-type literals. |
| `siteconfig.py` | PV/inverter/turbine configs; §12 range validation; every assumption labelled. |
| `codeid.py` | `code_version` content digest over the owned source tree (no git here). |
| `axis.py` | Regular time axis; the only duration→index converter (invariant 6). |
| `physical.py` | NOAA/Meeus solar position, Erbs decomposition, Liu–Jordan/Hay–Davies transposition, SAPM/Faiman/NOCT cell temp, PCHIP inverter curve, wind density/shear/curve, and the three Jensen entry points. |
| `features.py` | Single leakage choke point (invariant 1). |
| `splits.py` | Chronological 60/10/10/20 blocks + embargo; two distinct key types. |
| `quantile.py` | Linear QR (HiGHS LP) + numpy GBM quantile trees; rearrangement; clip. |
| `calibration.py` | CQR base + EnbPI LOO + ACI, with the §6.5 admissibility rule. |
| `scenarios.py` | Block bootstrap + Gaussian copula + Higham + fast-forward reduction + the independence-understates-tail demonstration. |
| `metrics.py` | MAE/RMSE/nMAE/bias/ramp, pinball, CRPS (ensemble + quantile-grid), coverage+Wilson, sharpness, Winkler, PIT+KS, energy, variogram, Diebold–Mariano, and the evaluation-only DSM proxy. |
| `artifacts.py` | Versioned artifacts, digest over array CONTENTS (not file bytes). |
| `interface.py` | Typed boundary with the six-decimal rule and the coverage attestation. |
| `datasets/` | Canonical schema + unit/sentinel map + §12 validation, content-addressed cache, four loaders (Open-Meteo, NASA POWER, GEFCom2014, EMSx). |

## No new dependencies

The package installs nothing (design §3.1). It uses only the already-present numpy, scipy,
pydantic and httpx. The GBM, conformal methods, copula, Higham projection and scenario reduction
are implemented directly on numpy/scipy for auditability and determinism. No novelty is claimed
for any of these established methods (conformal prediction, quantile regression, copulas, PV/wind
physics, scoring rules — see the design's §21 references).

## Assumptions and sensitivities

Every self-chosen number lives in `experiments/configs/*.json` or `siteconfig.py` with
`label="scenario_assumption"`, a `source`, and a `sensitivity` note. The headline families:
PV geometry/losses/inverter table; irradiance geometry constants; wind curve/shear/density;
axis windows and GBM hyperparameters; calibration `alpha`/`gamma`/windows; scenario `N_raw`/`K`/
block length; metric weights and CRPS truncation; regime thresholds; split fractions and embargo;
operational thresholds; and the DSM bands (taken from the project brief, unverified from the
primary regulation text). See design §17 for the full register.

## Validation tiers and claim licensing

- **Tier A** (measured power + as-issued forecasts: GEFCom2014, EMSx): licenses skill of the
  residual + calibration layer over the stated `baseline_source`. Does NOT license PV/wind
  conversion-chain accuracy (no obtained dataset supplies site geometry) or Indian-plant accuracy.
- **Tier B** (as-issued NWP vs reanalysis truth through our own chain): licenses weather-error
  propagation and calibration behaviour. Cannot validate the conversion model (chain bias cancels).
- **Tier C** (self-generated): licenses metric-implementation correctness, the two numerical
  demonstrations, determinism, and interface shape only — never a skill or coverage number.

## What is NOT validated (honest limitations)

1. No Indian plant telemetry; the Indian site configs are plausibility assumptions.
2. No 15-minute-resolution validation except on EMSx; everything else obtained is hourly.
3. Tier B cannot validate the conversion model.
4. The PV/wind conversion chains are never validated against measured power on any dataset
   (neither GEFCom2014 nor EMSx ships site geometry; EMSx `capacity`/`power` are battery ratings).
5. The chain is not cross-validated against pvlib unless the optional script is run.
6. As-issued intraday (<24 h lead) NWP is not obtainable from Open-Meteo Previous Runs.
7. GEFCom wind power curves are estimated without air-density normalization.
8. GEFCom solar irradiance is an hourly-mean (de-accumulated SSRD) with Erbs GHI-only bias.
9. Conditional coverage is not guaranteed; EnbPI/ACI give approximate/marginal guarantees.
10. Regime-sliced coverage claims need ≥ 24 months; below that they come from rolling-origin folds.
11. Demand/price models are not validated for the Indian market.
12. Block bootstrap cannot extrapolate beyond historical extremes; the copula imposes zero tail
    dependence and a separable space-time structure whose measured departure is reported.
13. Constant wake loss; 14. steady-state thermal; 15. DSM slab rates/bands unverified and hourly
    block except on EMSx; 16. no causal IRR/bps attribution; 17. determinism verified same-machine,
    same-process; 18. provider licence terms not independently re-confirmed this pass;
    19. compute coverage is budgeted, not exhaustive.

### Datasets obtained in this build

At build time the execution environment had no reliable network. `run_fetch_datasets.py` reports
each source's actual outcome. Where a source could not be obtained, every Tier A/B conclusion that
depended on it is **withheld** (listed under "datasets not obtained — conclusions withheld" in
`run_full_eval.py`'s artifact). **No synthetic series is substituted for missing real data.** The
offline CI path exercises the machinery and the two required numerical demonstrations on Tier C
data only.

**Acquisition is gated so the manifest only claims what was actually received.** The
content-addressed cache rejects a zero-length body, a payload below the per-source
plausible-dataset floor (`min_bytes` in `experiments/configs/datasets.json`, derived from the
real file sizes in design §8.1), and an HTML `Content-Type` (a record landing or error page) as
`DatasetUnavailable`. `run_fetch_datasets.py` therefore reports `obtained: true` only when a
plausible dataset payload was received, and `obtained: false` with a reason otherwise — so a
234 KB Zenodo record landing page or a 0-byte Open-Meteo body is recorded as not obtained, not
pinned with a content-addressed digest as if it were the dataset.

## Dataset licences

- Open-Meteo APIs: CC-BY-4.0 (only this sample may be committed). Licence page cited, not
  independently re-confirmed this pass.
- NASA POWER: public (NASA Langley Research Center).
- GEFCom2014: Elsevier journal supplementary archive — raw data NOT redistributed (digest/URL only).
- EMSx: Zenodo record 5510400, ODC-ODbL (share-alike) — raw data NOT redistributed.

## Experiments (one command each)

See `experiments/README.md`. Every experiment emits `experiments/results/<experiment>/<run_id>.json`;
`run_full_eval.py --twice` asserts an identical `determinism_digest` across two seeded runs.

### Baselines (§8.9)

`run_baselines.py` scores the full §8.9 baseline set on **one shared evaluation mask** and
reports wins **and** losses with a Diebold–Mariano p-value for every method versus `ours`:

| Baseline | Point forecast (as scored on Tier C synthetic) |
| --- | --- |
| `persistence` | last observed value held |
| `seasonal_climatology` | flat training-block mean |
| `baseline_only` | per-time-of-day training-block climatology (the honest "no residual model" baseline chain; on Tier C the baseline is climatology per §8.3, so it is distinct from the flat `seasonal_climatology`) |
| `qr_uncalibrated` | linear QR P50, raw, with no conformal step |
| `ours` | synthetic as-issued forecast proxy |

Every method consumes the same information cut-off and the same metric path (MAE and the τ=0.5
check loss). This run licenses only the fairness machinery and determinism; a real skill claim
requires Tier A data and is withheld.

### EMSx two-pass melt

`datasets/emsx.py:melt_rows` performs a genuine two-pass melt: pass 1 is a cheap counting pass
that validates each row's field count before any array is allocated, and pass 2 fills the
preallocated arrays. The final `cursor != total` guard is therefore a true two-pass invariant
(it cannot be reached by a mid-row resize), consistent with design §3.1/§8.4.3.

## Integration (for a later phase — not performed here)

`interface.build_calibrated_bands` / `build_joint_scenarios` are the typed entry points. Mapping
`CalibratedBand -> contracts.ForecastBand` discards six of nine quantile levels and needs a new
field (or separate artifact) for the coverage attestation; the 12-dim estimated dependence does
not fit the current 4×4 `ScenarioSet.matrix`; `ScenarioPath` has no `probability`. These are
contract changes a later phase makes (design §9.6). This phase writes none of them.
