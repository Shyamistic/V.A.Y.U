"""Read-only evidence assembly for the portfolio decision desk."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .canonical import sha256_hex
from .contracts import PortfolioFixture, ScenarioSet
from .dispatch import available_generation, demand_by_load, effective_tie_capacity
from .fixtures import load_demo_events, load_demo_obligations
from .forecasts import build_forecasts
from .scale_projection import build_scale_projection
from .software_bench_validation import SoftwareBenchEmulator, SoftwareBenchEnvelopeConfig


def experiment_reports(root: Path) -> dict[str, Any]:
    reports = []
    issues = []
    if not root.exists():
        return {"status": "unavailable", "reports": [], "issues": ["Experiment artifacts are not installed."]}
    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        candidates = []
        for path in sorted(directory.glob("*.json")):
            try:
                if path.stat().st_size > 5_000_000:
                    raise ValueError("artifact exceeds the workspace size limit")
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or not isinstance(data.get("created_at"), str):
                    raise ValueError("artifact has no recorded timestamp")
                candidates.append((data["created_at"], path.name, data))
            except (OSError, ValueError) as exc:
                issues.append(f"{directory.name}/{path.name}: {exc}")
        if candidates:
            _, name, data = max(candidates, key=lambda item: (item[0], item[1]))
            reports.append({
                "experiment": directory.name, "artifact": f"experiments/results/{directory.name}/{name}",
                "artifact_digest": sha256_hex(data), "history_count": len(candidates),
                "result": {key: value for key, value in data.items() if key not in {"config", "environment"}},
            })
    return {"status": "recorded" if reports else "unavailable", "reports": reports, "issues": issues}


def assemble_workspace_evidence(portfolio: PortfolioFixture, replay: dict[str, Any], experiment_root: Path, metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    scenarios = ScenarioSet.model_validate(replay["scenario_set"])
    path = next(item for item in scenarios.paths if item.scenario_id == replay["realized_path_id"])
    events = load_demo_events()
    generations = [available_generation(portfolio, path, events, slot) for slot in range(96)]
    loads = [demand_by_load(portfolio, path, events, slot) for slot in range(96)]
    generation_ids = sorted({asset_id for frame in generations for asset_id in frame})
    load_ids = sorted({asset_id for frame in loads for asset_id in frame})
    availability = {
        asset.asset_id: [not any(event.event_type == "bess_outage" and asset.asset_id in event.affected_ids and event.start_slot <= slot <= event.end_slot for event in events) for slot in range(96)]
        for asset in portfolio.assets if asset.kind == "bess"
    }
    cases = []
    for case_id, arguments in [("normal", {}), ("flex_reduced", {"cmd_flex_b_enable": False}), ("heartbeat_lost", {"time_since_heartbeat_ms": 2001})]:
        emulator = SoftwareBenchEmulator()
        cases.append({"case_id": case_id, "frame": asdict(emulator.step(**arguments))})
    cases.append({"case_id": "disconnected", "frame": asdict(SoftwareBenchEmulator().manual_emergency_disconnect())})
    return {
        "events": [item.model_dump(mode="json") for item in events],
        "forecasts": [item.model_dump(mode="json") for item in build_forecasts(portfolio)],
        "asset_series": {
            "generation_mw": {asset_id: [frame.get(asset_id, 0.0) for frame in generations] for asset_id in generation_ids},
            "demand_mw": {asset_id: [frame.get(asset_id, 0.0) for frame in loads] for asset_id in load_ids},
            "bess_available": availability,
            "tie_capacity_mw": [effective_tie_capacity(portfolio, events, slot) for slot in range(96)],
        },
        "obligations": [item.model_dump(mode="json") for item in load_demo_obligations()],
        "science": experiment_reports(experiment_root),
        "forecast_integration": "BASELINE_BANDS_ONLY_PHASE2_NOT_IN_DISPATCH",
        "hardware": {"connection": "NOT_CONNECTED", "mode": "SOFTWARE_ONLY", "config": asdict(SoftwareBenchEnvelopeConfig()), "cases": cases},
        "scale_projection": asdict(build_scale_projection(portfolio, metrics or {"values": []})),
    }