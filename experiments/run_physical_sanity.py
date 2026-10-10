r"""Physical-chain sanity: Jensen gap (both signs), transposition spread.

On synthetic Tier C wind this demonstrates E[f(V)] != f(E[V]) with both signs and
magnitudes in MW and % of capacity. SZA cross-check and Erbs-vs-measured penalty
require real Open-Meteo/NASA POWER data; when unobtainable those conclusions are
withheld.

Command:
    & $py .\monsoon_grid\experiments\run_physical_sanity.py --config .\monsoon_grid\experiments\configs\physical.json
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from _harness import ExperimentRun, load_config

from monsoon_grid.forecasting import physical as ph
from monsoon_grid.forecasting.siteconfig import TurbineConfig

REPO = Path(__file__).resolve().parents[1]


def jensen_demo(rng) -> dict:
    t = TurbineConfig(site_id="w_demo", ac_capacity_mw=10.0)
    cap = t.ac_capacity_mw
    # Convex ramp region.
    center = (t.v_cut_in_ms + t.v_rated_ms) / 2.0
    v_conv = center + rng.uniform(-2.0, 2.0, size=5000)
    mean_f_conv = float(np.mean(ph.power_from_speed(v_conv, t)))
    f_mean_conv = float(ph.power_from_speed(np.array([center]), t)[0])
    # Concave region near rated.
    near = t.v_rated_ms - 0.5
    v_conc = near + rng.uniform(-1.5, 1.5, size=5000)
    mean_f_conc = float(np.mean(ph.power_from_speed(v_conc, t)))
    f_mean_conc = float(ph.power_from_speed(np.array([near]), t)[0])
    return {
        "convex": {
            "mean_f_minus_f_mean_mw": mean_f_conv - f_mean_conv,
            "pct_capacity": 100.0 * (mean_f_conv - f_mean_conv) / cap,
            "sign": "mean(f)>f(mean)",
        },
        "concave_near_rated": {
            "mean_f_minus_f_mean_mw": mean_f_conc - f_mean_conc,
            "pct_capacity": 100.0 * (mean_f_conc - f_mean_conc) / cap,
            "sign": "mean(f)<f(mean)",
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg = load_config(args.config)
    run = ExperimentRun(
        experiment="physical_sanity",
        config=cfg,
        seed=cfg["seed"],
        dataset_fingerprints={"synthetic_tier_c": "self_generated"},
        results_root=REPO / "experiments" / "results",
    )
    gens = run.generators(["physical"])
    run.record("jensen_gap_tier_c", jensen_demo(gens["physical"]))
    run.record(
        "withheld_conclusions",
        [
            "SZA cross-check (needs NASA POWER)",
            "Liu-Jordan vs Hay-Davies POA/AC spread on real site-year (needs Open-Meteo + geometry)",
            "Erbs decomposition penalty (needs measured DNI/DHI)",
        ],
    )
    out = run.write()
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
