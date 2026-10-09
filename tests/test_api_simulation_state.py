"""A7 regression: /api/simulation/state is derived from the engine functions."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from monsoon_grid import api
from monsoon_grid.dispatch import available_generation, demand_by_load, effective_tie_capacity
from monsoon_grid.fixtures import load_demo_events, load_demo_portfolio
from monsoon_grid.scenarios import generate_scenarios


@pytest.fixture()
def client():
    return TestClient(api.app)


@pytest.fixture(scope="module")
def context():
    portfolio = load_demo_portfolio()
    events = load_demo_events()
    path = generate_scenarios(portfolio, seed=portfolio.seed).paths[1]
    return portfolio, events, path


@pytest.mark.parametrize("slot", [0, 20, 30, 44])
def test_state_matches_engine(client, context, slot):
    portfolio, events, path = context
    resp = client.get(f"/api/simulation/state?slot={slot}")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["total_renewable_mw"] == pytest.approx(round(sum(available_generation(portfolio, path, events, slot).values()), 6), abs=1e-6)
    assert data["industrial_demand_mw"]["total_mw"] == pytest.approx(round(sum(demand_by_load(portfolio, path, events, slot).values()), 6), abs=1e-6)
    assert data["grid_tie"]["capacity_mw"] == pytest.approx(effective_tie_capacity(portfolio, events, slot), abs=1e-6)
    flow = data["grid_tie"]["flow_mw"]
    assert data["grid_tie"]["headroom_mw"] == pytest.approx(data["grid_tie"]["capacity_mw"] - abs(flow), abs=1e-6)


def test_tie_derate_active_at_slot_44(client):
    data = client.get("/api/simulation/state?slot=44").json()["data"]
    assert data["grid_tie"]["capacity_mw"] == pytest.approx(48.0, abs=1e-6)


def test_slot_out_of_range_rejected(client):
    assert client.get("/api/simulation/state?slot=96").status_code == 422
    assert client.get("/api/simulation/state?slot=-1").status_code == 422


def test_unknown_replay_rejected(client):
    assert client.get("/api/simulation/state?slot=0&replay_id=does-not-exist").status_code == 404
