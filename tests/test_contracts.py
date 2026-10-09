import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from monsoon_grid.contracts import ASSET_ADAPTER, BessState, WeatherContext
from monsoon_grid.fixtures import load_demo_portfolio


def test_roster_and_strict_weather_contracts():
    portfolio = load_demo_portfolio()
    assert sum(asset.kind == "solar_farm" for asset in portfolio.assets) == 5
    assert sum(asset.kind == "wind_farm" for asset in portfolio.assets) == 3
    assert sum(asset.kind == "bess" for asset in portfolio.assets) == 2
    assert sum(asset.kind == "load_class" for asset in portfolio.assets) == 3
    assert all(asset.record_id for asset in portfolio.assets)
    with pytest.raises(ValidationError):
        WeatherContext.model_validate_json(json.dumps({"regime": "clear", "context_timestamp": "2026-07-01T00:00:00Z", "source_version": "x", "quality": "fresh", "power_mw": 4.0, "provenance": {"label": "scenario_assumption", "source_id": "x", "source_version": "1", "input_refs": []}}))


def test_valid_and_invalid_contract_fixtures():
    root = Path(__file__).parents[1] / "fixtures"
    valid = json.loads((root / "valid_contracts.json").read_text())
    invalid = json.loads((root / "invalid_contracts.json").read_text())
    assert ASSET_ADAPTER.validate_json(json.dumps(valid[0])).kind == "bess"
    for item in invalid:
        with pytest.raises(ValidationError):
            if item.get("kind") == "bess":
                ASSET_ADAPTER.validate_json(json.dumps(item))
            else:
                WeatherContext.model_validate_json(json.dumps(item))


def test_bess_state_derives_soc():
    portfolio = load_demo_portfolio()
    bess = next(asset for asset in portfolio.assets if asset.asset_id == "bess-a")
    state = BessState(asset_id="bess-a", energy_mwh=14.0, capacity_mwh=bess.capacity_mwh, reserve_mwh=bess.reserve_mwh, provenance=bess.provenance)
    assert state.soc_fraction == 0.7
    with pytest.raises(ValidationError):
        BessState(asset_id="bess-a", energy_mwh=3.0, capacity_mwh=20.0, reserve_mwh=4.0, provenance=bess.provenance)
