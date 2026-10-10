from fastapi.testclient import TestClient

from monsoon_grid.api import app


def test_api_health_openapi_and_no_live_control_route():
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    schema = client.get("/openapi.json").json()
    assert "/validate" in schema["paths"] and "/demo" in schema["paths"]
    assert not any("live" in path.lower() or "scada" in path.lower() for path in schema["paths"])


def test_idempotency_header_is_required():
    client = TestClient(app)
    response = client.post("/demo", json={"idempotency_key": "api-test", "deadline_seconds": 0.1})
    assert response.status_code == 422


def _secured_client(principal):
    from fastapi import FastAPI
    from monsoon_grid import auth, observability

    class Verifier:
        def verify(self, authorization, *, now):
            if authorization != "Bearer test-identity":
                raise auth.AuthRequired()
            return principal

    secured = FastAPI()
    config = auth.AuthConfig.from_env({"MONSOON_GRID_ENV": "development"})
    secured.add_middleware(auth.AuthMiddleware, config=config, verifier=Verifier())
    secured.add_middleware(observability.RequestContextMiddleware, registry=observability.install_metrics())
    secured.mount("/", app)
    return TestClient(secured)


def _principal(tenant="t-pilot", roles=("approver",)):
    from monsoon_grid.auth import Principal

    return Principal(actor_id="oidc:verified-user", subject="verified-user", issuer="test-issuer",
                     auth_method="google_oidc", roles=frozenset(roles), tenant_id=tenant,
                     token_fingerprint="a" * 64, expires_at=None, email=None, email_verified=False)


def test_product_approval_ignores_forged_actor_and_denies_self_approval(monkeypatch):
    from types import SimpleNamespace
    from monsoon_grid import api, auth

    principal = _principal()
    monkeypatch.setattr(api, "REPLAYS", api.ReplayRegistry())
    monkeypatch.setattr(api, "DECISION_AUTHORS", auth.DecisionAuthorIndex())
    monkeypatch.setattr(api, "_state_digest", lambda record: "a" * 64)
    monkeypatch.setattr(api, "_idempotency", lambda *args: None)
    monkeypatch.setattr(api, "_remember", lambda *args: None)
    api.REPLAYS["security-replay"] = api.ReplayRecord(execution=SimpleNamespace(decisions={}, proposals={}))
    body = {"replay_id": "security-replay", "decision_id": "security-decision",
            "actor_id": "forged-admin", "action": "reject", "reason": "operator review",
            "expected_state_digest": "a" * 64, "idempotency_key": "security-reject-01"}
    with _secured_client(principal) as client:
        assert client.post("/approvals", json=body).status_code == 401
        response = client.post("/approvals", json=body, headers={"Authorization": "Bearer test-identity"})
        assert response.status_code == 200
        assert response.json()["data"]["actor_id"] == principal.actor_id
        api.DECISION_AUTHORS.record_author(replay_id="security-replay", decision_id="security-decision", subject=principal.subject, author_class="human")
        response = client.post("/approvals", json=body, headers={"Authorization": "Bearer test-identity"})
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "APPROVAL_SELF_APPROVAL"


def test_product_tenant_cannot_read_foreign_replay_or_pilot_artifacts(monkeypatch):
    from types import SimpleNamespace
    from monsoon_grid import api

    monkeypatch.setattr(api, "REPLAYS", api.ReplayRegistry())
    api.REPLAYS["pilot-replay"] = api.ReplayRecord(execution=SimpleNamespace())
    with _secured_client(_principal(tenant="t-foreign", roles=("viewer",))) as client:
        headers = {"Authorization": "Bearer test-identity"}
        assert client.get("/metrics/pilot-replay", headers=headers).status_code == 404
        assert client.get("/api/workspace", headers=headers).status_code == 404
        assert client.get("/certificates/pilot-certificate", headers=headers).status_code == 404


def test_workspace_reports_missing_evidence_without_inventing_values(monkeypatch, tmp_path):
    from monsoon_grid import api

    monkeypatch.setattr(api, "ARTIFACT_ROOT", tmp_path)
    response = TestClient(app).get("/api/workspace")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "EVIDENCE_UNAVAILABLE"


def test_workspace_normalizes_slot_keyed_decisions(monkeypatch, tmp_path):
    import json
    from monsoon_grid import api
    from monsoon_grid.contracts import Provenance, SafetyDecision
    from monsoon_grid.fixtures import load_demo_portfolio
    from monsoon_grid.scenarios import generate_scenarios

    def decision(slot):
        return SafetyDecision(decision_id=f"workspace-decision-{slot}", replay_id="workspace-replay",
                              slot_index=slot, status="ACCEPTED", approved=False,
                              critical_unserved_mwh=0, reserve_shortfall_mwh=0,
                              violations=[], recovery_options=[], validator_version="test-v1",
                              proposal_digest="a" * 64,
                              provenance=Provenance(label="derived", source_id="test", source_version="test-v1", input_refs=["workspace-replay"])).model_dump(mode="json")

    metrics = {"values": []}
    replay = {"replay_id": "workspace-replay", "slot_states": [{}] * 96,
              "decisions": {"41": decision(41), "2": decision(2)}}
    scenarios = generate_scenarios(load_demo_portfolio())
    replay.update(scenario_set=scenarios.model_dump(mode="json"), realized_path_id=scenarios.realized_path_id)
    for name, data in {"replay-results.json": replay, "demo-summary.json": {"replay_id": "workspace-replay", "metrics": metrics},
                       "metrics.json": metrics, "certificate.json": {"certificate_id": "workspace-certificate"}}.items():
        (tmp_path / name).write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(api, "ARTIFACT_ROOT", tmp_path)
    response = TestClient(app).get("/api/workspace")
    assert response.status_code == 200
    assert [item["slot_index"] for item in response.json()["data"]["replay"]["decisions"]] == [2, 41]
    evidence = response.json()["data"]
    assert evidence["hardware"]["connection"] == "NOT_CONNECTED"
    assert evidence["hardware"]["mode"] == "SOFTWARE_ONLY"
    assert evidence["asset_series"]["bess_available"]["bess-b"][36] is False
    assert evidence["asset_series"]["tie_capacity_mw"][41] == 48.0
    assert evidence["forecast_integration"] == "BASELINE_BANDS_ONLY_PHASE2_NOT_IN_DISPATCH"


def test_science_reports_select_recorded_timestamp_not_filename(tmp_path):
    import json
    from monsoon_grid.workspace_evidence import experiment_reports

    directory = tmp_path / "fetch_datasets"
    directory.mkdir()
    for name, timestamp, obtained in [("z-old.json", "2026-10-09T09:00:00Z", True), ("a-new.json", "2026-10-09T11:00:00Z", False)]:
        (directory / name).write_text(json.dumps({"created_at": timestamp, "datasets_probe": {"emsx": {"obtained": obtained}}}), encoding="utf-8")
    report = experiment_reports(tmp_path)["reports"][0]
    assert report["result"]["datasets_probe"]["emsx"]["obtained"] is False
    assert report["history_count"] == 2
    assert report["artifact"].endswith("a-new.json")


def test_agent_deliberate_endpoint_returns_autonomous_decision():
    client = TestClient(app)
    response = client.get("/api/agent/deliberate", params={"slot": 41, "policy": "balanced"})
    assert response.status_code == 200
    data = response.json()["data"]
    # Autonomous ordering with a forced safety tier first.
    assert data["chosen_objective_order"][0] in {
        "critical_service_margin", "required_reliability_tier", "same_non_negotiable_safety_tier",
    }
    assert data["brain"] in {"deterministic", "gemini"}
    assert len(data["steps"]) >= 1
    # Honesty: the agent never does worse than the preset on critical reliability.
    assert data["reliability_regret_mwh"] <= 1e-6
    # Safety: forbidden tools are reported blocked.
    assert "submit_market_order" in data["forbidden_tools_blocked"]
    assert "NO LIVE CONTROL" in data["scope"]
    # The agent takes concrete actions across all four P4 categories, with a conflict note.
    categories = {action["category"] for action in data["actions"]}
    assert categories == {"battery", "market", "renewable", "demand"}
    assert data["conflict_note"]


def test_agent_deliberate_rejects_bad_slot():
    client = TestClient(app)
    assert client.get("/api/agent/deliberate", params={"slot": 999}).status_code == 422
    assert client.get("/api/agent/deliberate", params={"slot": 0, "policy": "nope"}).status_code == 422
