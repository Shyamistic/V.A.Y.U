import pytest
from pydantic import ValidationError

from monsoon_grid.contracts import BessState, GridState
from monsoon_grid.fixtures import load_demo_portfolio


def test_bess_energy_equation_and_grid_flow():
    portfolio = load_demo_portfolio()
    bess = next(asset for asset in portfolio.assets if asset.asset_id == "bess-a")
    before = 14.0
    after = before + 2.0 * bess.eta_c * 0.25 - 3.0 * 0.25 / bess.eta_d
    assert round(after, 6) == 13.659783
    with pytest.raises(ValidationError):
        GridState(import_mw=10.0, export_mw=2.0, flow_mw=99.0, import_limit_mw=75.0, export_limit_mw=45.0, tie_capacity_mw=80.0, tie_ramp_mw_per_slot=20.0, previous_flow_mw=0.0, provenance=portfolio.provenance)


def test_soc_is_not_authoritative():
    portfolio = load_demo_portfolio()
    with pytest.raises(ValidationError):
        BessState(asset_id="bess-a", energy_mwh=14.0, capacity_mwh=20.0, reserve_mwh=4.0, soc_fraction=0.5, provenance=portfolio.provenance)
