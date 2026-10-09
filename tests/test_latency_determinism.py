"""A6 regression: measured latency is real and lives outside every digest.

Two replays into separate runtime roots produce byte-identical canonical
artifacts; latency.json is permitted to differ.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path

CANONICAL_ARTIFACTS = [
    "metrics.json",
    "baseline-metrics.json",
    "replay-results.json",
    "demo-summary.json",
    "quality-reports.json",
    "approval-events.json",
    "event-trace.jsonl",
    "certificate.json",
]


def _run_replay(root: Path):
    os.environ["MONSOON_GRID_RUNTIME_ROOT"] = str(root)
    import monsoon_grid.config as cfg
    importlib.reload(cfg)
    from monsoon_grid.certificates import demo_trust_environment
    for key, value in demo_trust_environment().items():
        os.environ.setdefault(key, value)
    import monsoon_grid.cli as cli
    importlib.reload(cli)
    execution = cli.execute_replay(seed=20260701, policy_mode="balanced")
    cli.write_execution_artifacts(execution)
    return root / "artifacts"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_two_replays_byte_identical_except_latency(tmp_path):
    a = _run_replay(tmp_path / "det-a")
    b = _run_replay(tmp_path / "det-b")

    # latency.json carries a real measured total.
    for artifacts in (a, b):
        latency = json.loads((artifacts / "latency.json").read_text(encoding="utf-8"))
        totals = [s["elapsed_seconds"] for s in latency["samples"] if s["phase"] == "total"]
        assert max(totals) > 0.0

    for name in CANONICAL_ARTIFACTS:
        assert _digest(a / name) == _digest(b / name), name
    for slot_file in sorted((a / "decisions").glob("slot-*.json")):
        assert _digest(slot_file) == _digest(b / "decisions" / slot_file.name), slot_file.name

    # latency.json is excluded from the determinism guarantee (may differ).
    assert (a / "latency.json").exists() and (b / "latency.json").exists()
