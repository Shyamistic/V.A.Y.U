import datetime as dt
import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from monsoon_grid.assumptions import load_carbon_factor
from monsoon_grid.certificates import create_certificate, ensure_demo_trust, verify_certificate
from monsoon_grid.config import Settings
from monsoon_grid.contracts import Provenance, TelemetryRecord
from monsoon_grid.fixtures import load_demo_events, load_demo_portfolio
from monsoon_grid.persistence import PersistenceStore
from monsoon_grid.planner import plan_next
from monsoon_grid.quality import evaluate_quality, replay_time
from monsoon_grid.scenarios import generate_scenarios
from monsoon_grid.twin import run_twin
from monsoon_grid.validator import validate_proposal
from monsoon_grid.vayu_adapter import VayuContextAdapter


def test_scenario_planner_records_real_objectives_and_all_paths():
    portfolio = load_demo_portfolio()
    scenarios = generate_scenarios(portfolio)
    result = plan_next(portfolio, scenarios.paths[1], load_demo_events(), replay_id="planner-test", slot_index=0, policy_mode="balanced")
    assert len(scenarios.paths) == 5
    assert result.solver_result.method_hash != hashlib.sha256(b"scipy.optimize.milp/HiGHS;lexicographic-v1").hexdigest()
    if result.solver_result.status == "OPTIMAL":
        assert result.solver_result.objective_values
        assert len(result.solver_result.stage_optima) == 6
        assert result.solver_result.objective_values != [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]


def test_quality_gate_blocks_stale_flexible_dr_only():
    portfolio = load_demo_portfolio()
    now = replay_time(portfolio.fixture_start_utc, 40)
    telemetry = TelemetryRecord(
        telemetry_id="aux-stale",
        asset_id="deferrable_aux",
        signal="power_mw",
        value=9.0,
        unit="MW",
        observed_at=now - dt.timedelta(hours=2),
        received_at=now,
        quality="fresh",
        provenance=Provenance(label="scenario_assumption", source_id="test", source_version="1", input_refs=[]),
    )
    result = evaluate_quality(portfolio, replay_id="quality-test", slot_index=40, telemetry=[telemetry], signals=["flexible_noncritical_load"])
    assert result.decisions[0].quality == "stale"
    assert result.use_fallback
    assert not result.blocks_action


def test_twin_requires_matching_approval_before_mutation():
    portfolio = load_demo_portfolio()
    scenarios = generate_scenarios(portfolio)
    proposal_result = plan_next(portfolio, scenarios.paths[1], load_demo_events(), replay_id="approval-test", slot_index=0, policy_mode="balanced", scenario_paths=scenarios.paths)
    decision = validate_proposal(portfolio, scenarios.paths[1], load_demo_events(), proposal_result.proposal, replay_id="approval-test", slot_index=0)
    assert decision.status == "ACCEPTED"
    twin = run_twin(portfolio, scenarios.paths[1], load_demo_events(), {0: proposal_result.proposal}, {0: decision}, replay_id="approval-test", applied_slots={0})
    assert not twin.slot_states[0].action_applied


def test_validator_rejects_unknown_roster_and_contract_rejects_negative_values():
    portfolio = load_demo_portfolio()
    scenarios = generate_scenarios(portfolio)
    result = plan_next(portfolio, scenarios.paths[1], load_demo_events(), replay_id="roster-test", slot_index=0, policy_mode="balanced", scenario_paths=scenarios.paths)
    with pytest.raises(ValidationError):
        result.proposal.model_copy(update={"grid_import_mw": [-1.0]})
    malformed = result.proposal.model_copy(update={"renewable_dispatch_mw": {**result.proposal.renewable_dispatch_mw, "unknown-asset": [0.0]}})
    decision = validate_proposal(portfolio, scenarios.paths[1], load_demo_events(), malformed, replay_id="roster-test", slot_index=0)
    assert decision.status == "REJECTED"
    assert any(item.code == "UNKNOWN_ASSET" for item in decision.violations)


def test_vayu_requires_content_hash_and_only_reads_weather_context():
    path = Path(__file__).parents[1] / "fixtures" / "demo_weather_context.json"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    adapter = VayuContextAdapter(enabled=True, fixture_path=path, expected_sha256=digest)
    weather = adapter.read()
    assert adapter.status.state == "VAYU_CONTEXT_ONLY"
    assert set(adapter.authoritative_fields(weather)) == {"regime", "source_version", "quality"}
    with pytest.raises(Exception):
        VayuContextAdapter(enabled=True, fixture_path=path).read()


def test_certificate_validity_and_content_store_are_fail_closed(tmp_path):
    keyring, seed, fingerprint = ensure_demo_trust()
    settings = Settings(trust_keyring=keyring, trust_fingerprint=fingerprint, trust_valid_at="2026-07-01T00:00:00Z")
    import os
    os.environ["MONSOON_GRID_SIGNING_KEY"] = str(seed)
    store = PersistenceStore(tmp_path)
    certificate = create_certificate(replay_id="r", decision_id="d", decision_status="REJECTED", evidence={"replay_version": "monsoon-grid-replay-1"}, objects={"input": {"value": 1}}, settings=settings, store=store)
    assert verify_certificate(certificate, settings=settings, store=store)["verified"]
    with pytest.raises(Exception):
        verify_certificate(certificate, settings=Settings(trust_keyring=keyring, trust_fingerprint=fingerprint, trust_valid_at="2030-01-01T00:00:00Z"), store=store)


def test_record_ids_and_carbon_assumption_are_provenance_bound():
    portfolio = load_demo_portfolio()
    changed = portfolio.model_copy(update={"seed": portfolio.seed + 1})
    assert changed.record_id != portfolio.record_id
    carbon = load_carbon_factor()
    assert carbon.factor_version == "synthetic-carbon-factor-v1"
    assert carbon.provenance.label == "synthetic"
    assert carbon.provenance.content_sha256
