"""Tests for physics-informed solar POA, wind aerodynamics, BESS degradation, and quantile models."""

from __future__ import annotations

import numpy as np
import pytest

from monsoon_grid.physics_models import (
    BessDegradationParams,
    QuantileRegressor,
    SolarAssetParams,
    WindAssetParams,
    air_density_kg_per_m3,
    calculate_bess_degradation,
    calculate_density_corrected_wind_power,
    calculate_solar_poa_ac_power,
    empirical_coverage,
    evaluate_pinball_loss,
    solar_position,
)


def test_solar_position_and_poa() -> None:
    # At midnight (slot 0), elevation must be negative and power must be 0
    zenith, elev = solar_position(day_of_year=182, slot=0, latitude_deg=24.5, longitude_deg=72.0)
    assert elev < 0.0

    params = SolarAssetParams(asset_id="solar-01", capacity_mw=25.0)
    res_night = calculate_solar_poa_ac_power(params, slot=0, ghi_w_per_m2=0.0, dhi_w_per_m2=0.0, dni_w_per_m2=0.0, ambient_temp_c=25.0)
    assert res_night["ac_available_mw"] == 0.0
    assert not res_night["is_clipping"]

    # At solar noon (slot 48), high irradiance should yield positive AC power
    res_noon = calculate_solar_poa_ac_power(params, slot=48, ghi_w_per_m2=950.0, dhi_w_per_m2=150.0, dni_w_per_m2=850.0, ambient_temp_c=35.0)
    assert res_noon["poa_irradiance_w_per_m2"] > 500.0
    assert res_noon["ac_available_mw"] > 10.0
    assert res_noon["ac_available_mw"] <= 25.0  # Cannot exceed rated capacity


def test_wind_density_and_aerodynamics() -> None:
    rho = air_density_kg_per_m3(ambient_temp_c=25.0, pressure_hpa=1013.25)
    assert 1.15 < rho < 1.25

    params = WindAssetParams(asset_id="wind-01", capacity_mw=30.0)

    # Below cut-in speed (e.g. 2.0 m/s), power is zero
    res_calm = calculate_density_corrected_wind_power(params, wind_speed_ms=2.0)
    assert res_calm["delivered_wind_mw"] == 0.0

    # Above cut-out speed (e.g. 26.0 m/s), storm shutoff
    res_storm = calculate_density_corrected_wind_power(params, wind_speed_ms=26.0)
    assert res_storm["delivered_wind_mw"] == 0.0

    # At rated speed (e.g. 13.0 m/s), near rated output minus losses
    res_rated = calculate_density_corrected_wind_power(params, wind_speed_ms=13.0)
    assert res_rated["delivered_wind_mw"] > 25.0
    assert res_rated["delivered_wind_mw"] <= 30.0


def test_bess_degradation() -> None:
    params = BessDegradationParams(bess_id="bess-a", nominal_capacity_mwh=20.0)

    # Idle state has non-zero calendar aging but zero cycle aging
    res_idle = calculate_bess_degradation(params, charge_mw=0.0, discharge_mw=0.0, current_soc=0.50)
    assert res_idle["delta_soh_cycle"] == 0.0
    assert res_idle["delta_soh_calendar"] > 0.0

    # High C-rate deep discharge has significantly higher wear cost
    res_active = calculate_bess_degradation(params, charge_mw=0.0, discharge_mw=10.0, current_soc=0.15)
    assert res_active["delta_soh_cycle"] > 0.0
    assert res_active["marginal_wear_cost_usd"] > res_idle["marginal_wear_cost_usd"]


def test_quantile_regressor_and_metrics() -> None:
    np.random.seed(42)
    x = np.random.uniform(0.0, 10.0, size=(100, 2))
    # True target with heteroscedastic noise
    y = 2.5 * x[:, 0] + 1.2 * x[:, 1] + np.random.normal(0.0, 1.0 + 0.2 * x[:, 0], size=100)
    y = np.maximum(0.0, y)

    model = QuantileRegressor()
    model.fit(x, y)
    preds = model.predict(x)

    # Non-crossing property
    assert np.all(preds[0.10] <= preds[0.50] + 1e-6)
    assert np.all(preds[0.50] <= preds[0.90] + 1e-6)

    # Empirical coverage of 80% interval
    coverage = empirical_coverage(y, preds[0.10], preds[0.90])
    assert 0.65 <= coverage <= 0.95

    # Pinball loss is non-negative
    loss_p50 = evaluate_pinball_loss(y, preds[0.50], 0.50)
    assert loss_p50 > 0.0
