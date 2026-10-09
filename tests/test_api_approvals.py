"""A5 regression: audited interactive approvals are persisted and idempotent.

Fails on pre-change code where record_approval_and_events had no caller and the
store held zero approval rows.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from monsoon_grid import api
from monsoon_grid.fixtures import fixture_set_hash
from monsoon_grid.persistence import PersistenceStore


@pytest.fixture()
def client(tmp_path, monkeypatch):
    store = PersistenceStore(tmp_path / "store")
    monkeypatch.setattr(api, "STORE", store)
    api.REPLAYS.clear()
    api.IDEMPOTENCY.clear()
    return TestClient(api.app), store


def _create_and_reach_accepted(tc):
    created = tc.post(
        "/replays",
        json={"fixture_set_hash": fixture_set_hash(), "seed": 20260701, "policy_mode": "balanced", "adapter_state": "DISABLED", "idempotency_key": "k-create"},
        headers={"Idempotency-Key": "k-create"},
    ).json()
    replay_id = created["data"]["replay_id"]
    slot = created["data"]["slot_index"]
    digest = created["data"]["state_digest"]
    # Advance (planner-driven) until a slot returns an ACCEPTED decision needing approval.
    for attempt in range(96):
        resp = tc.post(
            f"/replays/{replay_id}/advance",
            json={"replay_id": replay_id, "expected_slot_index": slot, "expected_state_digest": digest, "approval_token": "none", "idempotency_key": f"k-adv-{attempt}"},
            headers={"Idempotency-Key": f"k-adv-{attempt}"},
        ).json()
        data = resp["data"]
        if data.get("approval_required"):
            return replay_id, data["slot_index"], data["state_digest"], data["decision"]
        slot = data["slot_index"]
        digest = data["state_digest"]
        if slot >= 96:
            break
    pytest.skip("no ACCEPTED decision requiring approval in the demo replay")


def test_interactive_approval_is_persisted_and_idempotent(client):
    tc, store = client
    replay_id, slot, digest, decision = _create_and_reach_accepted(tc)
    body = {
        "replay_id": replay_id,
        "decision_id": decision["decision_id"],
        "expected_state_digest": digest,
        "actor_id": "operator-jane",
        "action": "approve",
        "reason": "approved for shadow replay",
        "idempotency_key": "k-approve",
    }
    first = tc.post("/approvals", json=body, headers={"Idempotency-Key": "k-approve"})
    assert first.status_code == 200, first.text

    con = sqlite3.connect(store.db_path)
    rows = con.execute(
        "SELECT actor_id, synthetic_approval, replay_id, decision_id FROM approvals WHERE replay_id=? AND decision_id=?",
        (replay_id, decision["decision_id"]),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "operator-jane"
    assert rows[0][1] == 0
    event_rows = con.execute("SELECT COUNT(*) FROM events WHERE replay_id=?", (replay_id,)).fetchone()[0]
    assert event_rows >= 1
    con.close()

    # Same idempotency key -> byte-identical response, no second row.
    second = tc.post("/approvals", json=body, headers={"Idempotency-Key": "k-approve"})
    assert second.status_code == 200
    assert second.content == first.content

    con = sqlite3.connect(store.db_path)
    count = con.execute(
        "SELECT COUNT(*) FROM approvals WHERE replay_id=? AND decision_id=?",
        (replay_id, decision["decision_id"]),
    ).fetchone()[0]
    con.close()
    assert count == 1


def test_reserved_actor_is_rejected(client):
    tc, store = client
    replay_id, slot, digest, decision = _create_and_reach_accepted(tc)
    body = {
        "replay_id": replay_id,
        "decision_id": decision["decision_id"],
        "expected_state_digest": digest,
        "actor_id": "batch-replay-synthetic",
        "action": "approve",
        "reason": "should be forbidden",
        "idempotency_key": "k-reserved",
    }
    # The reserved actor is rejected at the contract layer (422 on request parse).
    resp = tc.post("/approvals", json=body, headers={"Idempotency-Key": "k-reserved"})
    assert resp.status_code == 422
