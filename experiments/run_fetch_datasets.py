r"""Re-probe + cache datasets, write a manifest. Honest obtained:false on failure.

NO synthetic stand-in: an unobtainable source is reported with its reason.

Command (PowerShell):
    & $py .\monsoon_grid\experiments\run_fetch_datasets.py --config .\monsoon_grid\experiments\configs\datasets.json
"""

from __future__ import annotations

import argparse
from pathlib import Path

from _harness import ExperimentRun, load_config

from monsoon_grid.forecasting.datasets.cache import ContentAddressedCache
from monsoon_grid.forecasting.errors import ForecastingError

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg = load_config(args.config)

    cache = ContentAddressedCache(REPO / cfg.get("cache_root", "data/cache"))
    run = ExperimentRun(
        experiment="fetch_datasets",
        config=cfg,
        seed=cfg["seed"],
        results_root=REPO / "experiments" / "results",
    )

    # Per-source plausible-payload floor (bytes). A source is only reported obtained
    # when a payload clearing this floor (and not an HTML landing/error page) is
    # actually received; otherwise obtained:false with a reason. Floors derive from
    # the real file sizes pinned in design sec 8.1 and are deliberately conservative
    # so a 234 KB record landing page or a 0-byte body cannot pass as the dataset.
    report = {}
    for name, src in cfg["sources"].items():
        min_bytes = int(src.get("min_bytes", cfg.get("default_min_bytes", 1)))
        try:
            entry = cache.fetch(src["endpoint"], min_bytes=min_bytes)
            report[name] = {
                "obtained": True,
                "sha256": entry.sha256,
                "observed_length": entry.meta.get("observed_length"),
                "content_type": entry.meta.get("content_type"),
                "min_bytes": min_bytes,
                "license": src.get("license"),
            }
        except ForecastingError as exc:
            report[name] = {
                "obtained": False,
                "reason": exc.code,
                "detail": str(exc),
                "min_bytes": min_bytes,
                "license": src.get("license"),
            }

    run.record("datasets_probe", report)
    out = run.write()
    print(f"wrote {out}")
    for name, r in report.items():
        status = "OBTAINED" if r.get("obtained") else f"NOT OBTAINED ({r.get('reason')})"
        print(f"  {name}: {status}")


if __name__ == "__main__":
    main()
