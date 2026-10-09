"""Derive and verify each digest-bearing fixture's content_sha256.

For every fixture whose assumption model carries a ``verify_fixture_lineage``
digest, this script re-derives the digest over exactly the payload subset the
model hashes and either writes it back (default) or checks it (``--check``).

No digest is ever typed by hand.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from monsoon_grid.canonical import sha256_hex  # noqa: E402
from monsoon_grid.config import FIXTURE_ROOT  # noqa: E402

# fixture file -> ordered list of top-level keys that the model hashes.
LINEAGE_FIXTURES: dict[str, list[str]] = {
    "flexibility_costs.json": ["flexibility_cost_version", "currency", "entries", "source_note"],
    "market_settlement.json": [
        "settlement_version",
        "currency",
        "submission_lag_slots",
        "auction_session_slots",
        "nonmarket_export_label",
        "nonmarket_export_rate_usd_per_mwh",
        "source_note",
    ],
    "dsm_regimes.json": ["regime_version", "currency", "regimes", "source_note"],
    "obligation_heuristic.json": [
        "heuristic_version",
        "comfort_margin_fraction",
        "risk_floor_fraction",
        "urgency_exponent",
        "price_cap_multiplier",
        "source_note",
    ],
}


def _derive(raw: dict, keys: list[str]) -> str:
    return sha256_hex({key: raw[key] for key in keys})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="verify only; exit nonzero on mismatch")
    args = parser.parse_args()

    failures: list[str] = []
    for name, keys in LINEAGE_FIXTURES.items():
        path = FIXTURE_ROOT / name
        raw = json.loads(path.read_text(encoding="utf-8"))
        derived = _derive(raw, keys)
        stored = raw.get("provenance", {}).get("content_sha256")
        if args.check:
            if stored != derived:
                failures.append(name)
                print(json.dumps({"file": name, "field": "content_sha256", "stored": stored, "derived": derived, "match": False}))
            else:
                print(json.dumps({"file": name, "field": "content_sha256", "match": True}))
        else:
            if stored != derived:
                raw["provenance"]["content_sha256"] = derived
                path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"file": name, "field": "content_sha256", "old": stored, "new": derived}))

    if args.check and failures:
        print(f"MISMATCH: {failures}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
