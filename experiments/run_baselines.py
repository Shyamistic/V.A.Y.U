r"""Baseline comparison on synthetic Tier C with a shared evaluation mask.

Evaluates the full §8.9 baseline set on one shared mask and reports wins AND
losses with counts and a Diebold-Mariano p-value for every method vs ``ours``:

- ``persistence``          -- last observed value held.
- ``seasonal_climatology`` -- flat training-block mean.
- ``baseline_only``        -- per-time-of-day climatology point (the honest
  "no residual model" baseline; on Tier C the baseline chain is climatology,
  §8.3/§8.9) scored as a point forecast.
- ``qr_uncalibrated``      -- linear QR P50, raw, no conformal step.
- ``ours``                 -- the synthetic as-issued forecast proxy.

Real skill claims require Tier A data and are withheld here: this run licenses
only the fairness machinery (one shared mask, identical metric path, wins and
losses, DM) and determinism, never a skill headline (design §8.2 Tier C).

Command:
    & $py .\monsoon_grid\experiments\run_baselines.py --config .\monsoon_grid\experiments\configs\baselines.json
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from _harness import ExperimentRun, load_config
from _synthetic import synthetic_as_issued_forecast, synthetic_pv_series

from monsoon_grid.forecasting.metrics import diebold_mariano, mae, pinball
from monsoon_grid.forecasting.quantile import fit_linear_quantile_model

REPO = Path(__file__).resolve().parents[1]

STEPS_PER_DAY = 96


def _tod_features(n: int) -> np.ndarray:
    """Time-of-day sin/cos on the 96-slot 15-min axis (shared feature basis)."""
    tod = (np.arange(n) % STEPS_PER_DAY) / STEPS_PER_DAY
    return np.column_stack([np.sin(2 * np.pi * tod), np.cos(2 * np.pi * tod)])


def _tod_climatology(y: np.ndarray, train_end: int) -> np.ndarray:
    """Per-time-of-day median of the training block, mapped onto every slot.

    This is the ``baseline_only`` point forecast: a climatology baseline chain
    (§8.3) with no residual model, distinct from the flat seasonal_climatology.
    """
    n = len(y)
    slot = np.arange(n) % STEPS_PER_DAY
    out = np.empty(n)
    for s in range(STEPS_PER_DAY):
        train_slot = y[:train_end][slot[:train_end] == s]
        out[slot == s] = float(np.median(train_slot)) if train_slot.size else 0.0
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg = load_config(args.config)
    run = ExperimentRun(
        experiment="baselines",
        config=cfg,
        seed=cfg["seed"],
        dataset_fingerprints={"synthetic_tier_c": "self_generated"},
        results_root=REPO / "experiments" / "results",
    )
    g = run.generators(["data"])
    _epoch, y = synthetic_pv_series(30, g["data"], 100.0)
    fcst = synthetic_as_issued_forecast(y, g["data"])

    n = len(y)
    train_end = int(0.6 * n)

    # Shared evaluation mask: last 20% of the series, used by EVERY method.
    mask = np.zeros(n, dtype=bool)
    mask[int(0.8 * n):] = True

    # --- point forecasts for every baseline (identical info cut-off) ---------
    persistence = np.concatenate([[0.0], y[:-1]])
    climatology = np.full(n, float(np.mean(y[:train_end])))
    baseline_only = _tod_climatology(y, train_end)

    # qr_uncalibrated: linear QR P50 on [forecast, tod-sin, tod-cos], raw.
    feats = np.column_stack([fcst, _tod_features(n)])
    qr_model = fit_linear_quantile_model(feats[:train_end], y[:train_end], taus=(0.5,))
    qr_uncalibrated = qr_model.predict(feats)[0]

    ours = fcst  # synthetic "ours" proxy

    preds = {
        "persistence": persistence,
        "seasonal_climatology": climatology,
        "baseline_only": baseline_only,
        "qr_uncalibrated": qr_uncalibrated,
        "ours": ours,
    }

    # Per-method losses on the shared mask (absolute for MAE/DM; check loss @0.5).
    abs_losses = {name: np.abs(y - p) for name, p in preds.items()}

    table = {
        name: {
            "mae": mae(y, preds[name], mask).value,
            "pinball_p50": pinball(y, preds[name], 0.5, mask).value,
        }
        for name in preds
    }

    # Wins AND losses vs ours with Diebold-Mariano for every other baseline.
    ours_mean_loss = float(np.mean(abs_losses["ours"][mask]))
    comparisons = {}
    for name in ["persistence", "seasonal_climatology", "baseline_only", "qr_uncalibrated"]:
        dm, p = diebold_mariano(abs_losses["ours"][mask], abs_losses[name][mask], h=cfg["diebold_mariano_h"])
        other_mean_loss = float(np.mean(abs_losses[name][mask]))
        comparisons[name] = {
            "dm_stat": dm,
            "p_value": p,
            "ours_mean_abs_loss": ours_mean_loss,
            "other_mean_abs_loss": other_mean_loss,
            "ours_wins": ours_mean_loss < other_mean_loss,
            "ours_loses": ours_mean_loss > other_mean_loss,
        }

    run.record("baselines_evaluated", list(preds.keys()))
    run.record("shared_mask_n", int(mask.sum()))
    run.record("baseline_metrics", table)
    run.record("comparisons_vs_ours", comparisons)
    run.record(
        "note",
        "Tier C synthetic -- fairness machinery and determinism only; no real skill claim. "
        "All five baselines scored on one shared mask with wins AND losses and Diebold-Mariano.",
    )
    out = run.write()
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
