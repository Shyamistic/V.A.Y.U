from monsoon_grid.fixtures import load_demo_portfolio
from monsoon_grid.scenarios import CORRELATION_MATRIX, generate_scenarios, quantile_interpolate, validate_correlation_matrix


def test_scenarios_are_repeatable_and_share_realized_path():
    portfolio = load_demo_portfolio()
    first = generate_scenarios(portfolio, seed=20260701)
    second = generate_scenarios(portfolio, seed=20260701)
    assert first.realized_path_id == "scenario-02"
    assert first.realized_path_sha256 == second.realized_path_sha256
    assert first.paths[1].model_dump_json() == second.paths[1].model_dump_json()
    assert len(first.paths) == 5 and len(first.paths[0].price_usd_per_mwh) == 96


def test_psd_and_piecewise_tails():
    validate_correlation_matrix(CORRELATION_MATRIX)
    assert quantile_interpolate(-20.0, 1.0, 2.0, 3.0) == 1.0
    assert quantile_interpolate(20.0, 1.0, 2.0, 3.0) == 3.0
