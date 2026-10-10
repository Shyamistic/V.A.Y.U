"""MonsoonGrid gate harness: G1/G2/G6/G9/G10 blocking, G3 warning.

Runs the full artifact-writing replay (including Ed25519 signing), then checks
each gate by reading the artifacts that were just written -- no gate re-derives
business logic. Exit code is 1 only if a BLOCKING gate fails; G3 never affects
the exit code.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

TOL = 1e-6
DT = 0.25


def _run_replay(runtime_root: str, seed: int, policy: str, skip_replay: bool):
    os.environ["MONSOON_GRID_RUNTIME_ROOT"] = str(Path(runtime_root).resolve())
    from monsoon_grid.config import RUNTIME_ROOT  # noqa: F401 - triggers path resolution
    import importlib
    import monsoon_grid.config as cfg
    importlib.reload(cfg)

    artifact_root = Path(runtime_root).resolve() / "artifacts"
    if skip_replay:
        return artifact_root, None
    # Signing must be available; fail closed otherwise (G6).
    try:
        from monsoon_grid.certificates import demo_trust_environment
        for key, value in demo_trust_environment().items():
            os.environ.setdefault(key, value)
    except Exception:
        return artifact_root, "CERT_SIGNING_UNAVAILABLE"
    from monsoon_grid.cli import execute_replay, write_execution_artifacts
    execution = execute_replay(seed=seed, policy_mode=policy)
    write_execution_artifacts(execution)
    return artifact_root, None


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _metric_map(values) -> dict:
    return {m["name"]: m["value"] for m in values}


def gate_g1(artifacts: Path) -> tuple[str, str]:
    replay = _read_json(artifacts / "replay-results.json")
    slots = replay["slot_states"]
    for state in slots:
        demand_mwh = round(state["demand_mw"], 6) * DT
        residual = state["served_mwh"] + state["critical_unserved_mwh"] + state["noncritical_unserved_mwh"] - demand_mwh
        if abs(residual) > TOL:
            return "FAIL", f"slot {state['slot_index']} balance residual {residual}"
    for state in slots:
        if not state["action_applied"] and state["demand_mw"] > 0:
            if state["critical_unserved_mwh"] + state["noncritical_unserved_mwh"] <= 0:
                return "FAIL", f"slot {state['slot_index']} unapplied with demand reports zero unserved"
    return "PASS", "energy accounting consistent; unapplied slots report unserved"


def gate_g2(artifacts: Path) -> tuple[str, str]:
    metrics = _metric_map(_read_json(artifacts / "metrics.json")["values"])
    if metrics.get("dr_shed_mwh", 0.0) > 0 and metrics.get("dr_cost_usd", 0.0) <= 0:
        return "FAIL", "dr_shed_mwh > 0 but dr_cost_usd == 0 (DR not priced)"
    replay = _read_json(artifacts / "replay-results.json")
    for state in replay["slot_states"]:
        if state["dr_shed_mwh"] > 0 and state["dr_cost_usd"] <= 0:
            return "FAIL", f"slot {state['slot_index']} dr_shed_mwh > 0 but dr_cost_usd == 0"
    total = sum(state["cost_usd"] for state in replay["slot_states"])
    if abs(metrics.get("net_cost_usd", 0.0) - total) > TOL:
        return "FAIL", f"net_cost_usd {metrics.get('net_cost_usd')} != sum of slot cost_usd {total}"
    return "PASS", "priced DR carries cost; net_cost rolls up from slots"


def gate_g6(artifacts: Path) -> tuple[str, str]:
    sources: dict[str, dict] = {}
    sources["metrics.json"] = _metric_map(_read_json(artifacts / "metrics.json")["values"])
    summary = _read_json(artifacts / "demo-summary.json")
    sources["demo-summary.metrics"] = _metric_map(summary["metrics"]["values"])
    # Compare every name present in >=2 sources.
    names = set()
    for src in sources.values():
        names |= set(src)
    for name in sorted(names):
        present = [(label, src[name]) for label, src in sources.items() if name in src]
        if len(present) < 2:
            continue
        base = present[0][1]
        for label, value in present[1:]:
            if isinstance(base, int) and isinstance(value, int):
                if base != value:
                    return "FAIL", f"metric {name} disagrees: {present}"
            elif base is None or value is None:
                if base is not value:
                    return "FAIL", f"metric {name} disagrees (null): {present}"
            elif abs(float(base) - float(value)) > TOL:
                return "FAIL", f"metric {name} disagrees: {present}"
    return "PASS", "cross-artifact metric values agree"


G9_STRINGS = ["12 ms", "OPTIMAL_FEASIBLE", "Zero Constraint", "Ed25519 Verified", "Deterministic HiGHS"]


def gate_g9() -> tuple[str, str]:
    ui_root = Path(__file__).resolve().parents[1] / "src" / "monsoon_grid" / "ui"
    for path in ui_root.rglob("*"):
        if not path.is_file() or "node_modules" in path.parts or path.suffix not in {".html", ".js", ".ts", ".tsx", ".css", ".json"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for index, line in enumerate(text.splitlines(), start=1):
            for banned in G9_STRINGS:
                if banned in line:
                    return "FAIL", f"{path.name}:{index} contains {banned!r}"
    return "PASS", "no forbidden UI capability strings"


ALLOWED_LABELS = {"measured", "derived", "synthetic", "scenario_assumption"}


def gate_g10(artifacts: Path) -> tuple[str, str]:
    files = ["metrics.json", "baseline-metrics.json"]
    value_lists = []
    for name in files:
        value_lists.extend(_read_json(artifacts / name)["values"])
    latency = artifacts / "latency.json"
    if latency.exists():
        value_lists.extend(_read_json(latency)["values"])
    for metric in value_lists:
        label = (metric.get("provenance") or {}).get("label")
        if label not in ALLOWED_LABELS:
            return "FAIL", f"metric {metric['name']} has invalid provenance label {label!r}"
        if metric.get("unit") is None and not metric.get("unit_justification"):
            if metric.get("status") == "available":
                return "FAIL", f"metric {metric['name']} has null unit and no unit_justification"
    return "PASS", "every metric carries a valid label and unit-or-justification"


def gate_g3(artifacts: Path, seed: int) -> tuple[str, str, dict]:
    """Non-anticipativity warning: compare committed BESS variables across single-path solves."""
    from monsoon_grid.fixtures import load_demo_events, load_demo_portfolio
    from monsoon_grid.planner import _build_model, _solve_lexicographic
    from monsoon_grid.config import POLICY_OBJECTIVES, DEFAULT_PER_REQUEST_BUDGET_SECONDS, DEFAULT_PER_SLOT_BUDGET_SECONDS
    from monsoon_grid.assumptions import load_carbon_factor
    from monsoon_grid.scenarios import generate_scenarios
    from time import monotonic

    portfolio = load_demo_portfolio()
    events = load_demo_events()
    scenarios = generate_scenarios(portfolio, seed=seed)
    carbon = load_carbon_factor()
    per_path_solutions: dict[str, dict[str, float]] = {}
    committed_keys: list[str] = []
    for path in scenarios.paths:
        model, _ = _build_model(portfolio, [path], events, slot_index=0, energy_state=None, previous_flow_mw=0.0, dr_allowed_load_ids=None, carbon_factor=carbon)
        bundle, status, _ = _solve_lexicographic(model, objective_order=POLICY_OBJECTIVES["balanced"], deadline=monotonic() + 30.0, request_budget_seconds=30.0, slot_budget_seconds=30.0)
        if bundle is None:
            continue
        solution = bundle[0]
        committed_keys = list(model.common_indexes["bess"].keys())
        per_path_solutions[path.scenario_id] = {key: float(solution[index]) for key, index in model.common_indexes["bess"].items()}

    binary_keys = {k for k in committed_keys if k.startswith("mode:")}
    discrepancies = []
    ids = sorted(per_path_solutions)
    for key in sorted(committed_keys):
        max_dev = 0.0
        pair = [ids[0], ids[0]] if ids else ["", ""]
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                dev = abs(per_path_solutions[ids[i]][key] - per_path_solutions[ids[j]][key])
                if dev > max_dev:
                    max_dev = dev
                    pair = [ids[i], ids[j]]
        discrepancies.append({"variable": key, "binary": key in binary_keys, "max_abs_deviation": round(max_dev, 6), "scenario_pair": pair})

    any_dev = any(d["max_abs_deviation"] > TOL for d in discrepancies)
    report = {
        "gate": "G3",
        "blocking": False,
        "status": "WARN" if any_dev else "PASS",
        "slot_index": 0,
        "tolerance": TOL,
        "committed_variable_source": "planner._build_model model.common_indexes[\"bess\"].keys()",
        "committed_variables": sorted(committed_keys),
        "excluded_variables_reason": "Renewable dispatch, grid flow, export split, DR, and unserved variables are scenario-indexed by construction; comparing them is noise, not a non-anticipativity signal.",
        "discrepancies": discrepancies,
        "reason": "The planner shares only BESS variables across scenarios and commits from the preselected realized path scenario-02. A genuinely non-anticipative commitment requires a multi-period rolling-horizon formulation.",
        "owner_workflow": "phase2-multiperiod-optimizer",
    }
    status = "WARN" if any_dev else "PASS"
    detail = f"{sum(1 for d in discrepancies if d['max_abs_deviation'] > TOL)} committed variable(s) deviate across scenarios"
    return status, detail, report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", default="runtime-gates")
    parser.add_argument("--seed", type=int, default=20260701)
    parser.add_argument("--policy", default="balanced")
    parser.add_argument("--skip-replay", action="store_true")
    args = parser.parse_args()

    artifacts, signing_error = _run_replay(args.runtime_root, args.seed, args.policy, args.skip_replay)

    results: list[tuple[str, str, bool, str]] = []  # gate, status, blocking, detail
    if signing_error:
        results.append(("G6", "FAIL", True, signing_error))
    else:
        s, d = gate_g1(artifacts); results.append(("G1", s, True, d))
        s, d = gate_g2(artifacts); results.append(("G2", s, True, d))
        s, d = gate_g6(artifacts); results.append(("G6", s, True, d))
        s, d = gate_g9(); results.append(("G9", s, True, d))
        s, d = gate_g10(artifacts); results.append(("G10", s, True, d))

    # G3 (warning, non-blocking)
    try:
        g3_status, g3_detail, g3_report = gate_g3(artifacts, args.seed)
    except Exception as exc:  # noqa: BLE001
        g3_status, g3_detail, g3_report = "WARN", f"G3 evaluation error: {exc}", {"gate": "G3", "blocking": False, "status": "WARN", "error": str(exc)}
    (artifacts / "gate-report.json").write_text(json.dumps(g3_report, indent=2, sort_keys=True), encoding="utf-8")

    for gate, status, blocking, detail in results:
        print(f"{gate} {status} {'BLOCKING' if blocking else 'WARNING'} — {detail}")
    print(f"G3 {g3_status} WARNING — {g3_detail}")

    blocking_fail = any(status == "FAIL" and blocking for _, status, blocking, _ in results)
    print(f"SUMMARY: {'FAIL' if blocking_fail else 'PASS'} (G3 is non-blocking)")
    return 1 if blocking_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
