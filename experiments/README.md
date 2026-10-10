# MonsoonGrid Phase 2 forecasting experiments

Each experiment has **one** documented PowerShell command and emits a machine-readable
artifact at `experiments/results/<experiment>/<run_id>.json`. No experiment installs anything
(design §3.1) and none fabricates data: an unobtainable dataset is reported `obtained: false`
with a reason and its conclusions are withheld.

## Environment (verbatim)

```powershell
$py  = 'C:\Users\shyam.BATCONSOLE\Desktop\etgenai\.venv\Scripts\python.exe'
$env:PYTHONPATH = 'C:\Users\shyam.BATCONSOLE\Desktop\etgenai\monsoon_grid\src;C:\Users\shyam.BATCONSOLE\Desktop\etgenai\monsoon_grid\runtime\site-packages'
Set-Location 'C:\Users\shyam.BATCONSOLE\Desktop\etgenai\monsoon_grid\experiments'
```

## Commands

```powershell
& $py .\run_fetch_datasets.py      --config .\configs\datasets.json
& $py .\run_physical_sanity.py     --config .\configs\physical.json
& $py .\run_quantile_training.py   --config .\configs\quantile.json
& $py .\run_calibration_compare.py --config .\configs\calibration.json
& $py .\run_scenario_study.py      --config .\configs\scenarios.json
& $py .\run_baselines.py           --config .\configs\baselines.json
& $py .\run_full_eval.py           --config .\configs\full_eval.json --twice
& $py .\run_pvlib_crosscheck.py    # optional; skips unless pvlib is importable
```

## What runs offline vs needs network

Without network, every script still runs and emits its artifact, exercising the four-stage
machinery and the two required numerical demonstrations (Jensen gap; independence understates
joint tail) on **self-generated Tier C data**. Tier C licenses ONLY metric-implementation
correctness, the demonstrations, determinism, and interface shape — never a skill or coverage
claim. Every Tier A/B conclusion is withheld and listed under "datasets not obtained" until the
real GEFCom2014 / EMSx / Open-Meteo / NASA POWER data is fetched.

`run_full_eval.py --twice` asserts byte-identical `determinism_digest` across two in-process
runs with the same seed.
