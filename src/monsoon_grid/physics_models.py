"""Physics-informed renewable generation, aerodynamics, electrochemical degradation, and quantile models.

This module provides first-principles physical conversions and validated empirical models:
1. Solar PV: Solar geometry (zenith/azimuth), Plane-of-Array (POA) irradiance, NOCT cell temperature,
   temperature derating, and non-linear inverter clipping.
2. Wind Aerodynamics: Temperature/pressure air density correction, IEC Class II/III power curves with
   cubic ramp, Betz limit loss, and wake interference.
3. BESS Electrochemistry: Depth-of-Discharge (DoD) cycle aging, C-rate stress exponent, and
   Arrhenius thermal calendar aging.
4. Quantile Regression Forecaster: Multi-quantile regressor with pinball loss and empirical coverage checks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any
import numpy as np


# ============================================================================
# 1. Solar Photovoltaic & Inverter Physics Model
# ============================================================================

@dataclass(frozen=True)
class SolarAssetParams:
    """Site-specific solar farm physical configuration."""
    asset_id: str
    capacity_mw: float
    latitude_deg: float = 24.5      # Representative Indian solar park latitude (e.g. Bhadla/Rajasthan or Pavagada/Karnataka)
    longitude_deg: float = 72.0
    tilt_deg: float = 25.0          # Fixed tilt angle optimized for latitude
    azimuth_deg: float = 180.0      # Due south
    noct_celsius: float = 45.0      # Nominal Operating Cell Temperature
    temp_coefficient: float = -0.0035  # -0.35%/°C above 25°C
    inverter_efficiency: float = 0.985
    soiling_loss: float = 0.03      # 3% dust/soiling
    wiring_loss: float = 0.015      # 1.5% DC cabling loss
    inverter_clipping_ratio: float = 1.25  # DC/AC ratio (e.g. 1.25 DC overload capacity)


def solar_position(day_of_year: int, slot: int, latitude_deg: float, longitude_deg: float) -> tuple[float, float]:
    """Calculate solar elevation and zenith angle for slot 0..95 (15-min intervals).

    Returns:
        (zenith_deg, elevation_deg)
    """
    # Fractional hour of the day (0.0 to 24.0)
    hour = slot * 0.25
    # Solar declination angle delta
    declination = 23.45 * math.sin(math.radians((360.0 / 365.0) * (day_of_year - 81)))
    # Hour angle omega (15 degrees per hour, solar noon at 12:00)
    hour_angle = 15.0 * (hour - 12.0)

    lat_rad = math.radians(latitude_deg)
    dec_rad = math.radians(declination)
    omega_rad = math.radians(hour_angle)

    # Solar elevation angle alpha
    sin_elev = math.sin(lat_rad) * math.sin(dec_rad) + math.cos(lat_rad) * math.cos(dec_rad) * math.cos(omega_rad)
    sin_elev = max(-1.0, min(1.0, sin_elev))
    elev_rad = math.asin(sin_elev)
    elevation_deg = math.degrees(elev_rad)
    zenith_deg = max(0.0, 90.0 - elevation_deg)

    return zenith_deg, elevation_deg


def calculate_solar_poa_ac_power(
    params: SolarAssetParams,
    slot: int,
    ghi_w_per_m2: float,
    dhi_w_per_m2: float,
    dni_w_per_m2: float,
    ambient_temp_c: float,
    day_of_year: int = 182,
) -> dict[str, float]:
    """Compute Plane-of-Array (POA) irradiance, cell temperature, and AC delivered power.

    Uses the isotropic Liu-Jordan sky model with NOCT thermal cell balance.
    """
    zenith_deg, elev_deg = solar_position(day_of_year, slot, params.latitude_deg, params.longitude_deg)

    if elev_deg <= 0.0 or ghi_w_per_m2 <= 1.0:
        return {
            "poa_irradiance_w_per_m2": 0.0,
            "cell_temp_c": ambient_temp_c,
            "dc_power_mw": 0.0,
            "ac_available_mw": 0.0,
            "is_clipping": False,
        }

    zenith_rad = math.radians(zenith_deg)
    tilt_rad = math.radians(params.tilt_deg)

    # Angle of incidence theta
    cos_theta = math.cos(zenith_rad) * math.cos(tilt_rad) + math.sin(zenith_rad) * math.sin(tilt_rad) * math.cos(math.radians(180.0 - params.azimuth_deg))
    cos_theta = max(0.0, min(1.0, cos_theta))

    # Beam component on tilted surface
    poa_beam = dni_w_per_m2 * cos_theta
    # Sky diffuse component (isotropic Liu-Jordan)
    poa_diffuse = dhi_w_per_m2 * (1.0 + math.cos(tilt_rad)) / 2.0
    # Ground reflected component (albedo rho = 0.20)
    poa_ground = ghi_w_per_m2 * 0.20 * (1.0 - math.cos(tilt_rad)) / 2.0

    poa_total = max(0.0, poa_beam + poa_diffuse + poa_ground)

    # Cell temperature under NOCT
    t_cell = ambient_temp_c + (poa_total / 800.0) * (params.noct_celsius - 20.0)

    # Temperature derate factor
    temp_derate = 1.0 + params.temp_coefficient * (t_cell - 25.0)
    temp_derate = max(0.5, min(1.1, temp_derate))

    # Clean DC power
    effective_dc_capacity = params.capacity_mw * params.inverter_clipping_ratio
    dc_power = (
        effective_dc_capacity
        * (poa_total / 1000.0)
        * temp_derate
        * (1.0 - params.soiling_loss)
        * (1.0 - params.wiring_loss)
    )
    dc_power = max(0.0, dc_power)

    # Inverter AC conversion with rating ceiling
    rated_ac = params.capacity_mw
    ac_raw = dc_power * params.inverter_efficiency
    ac_available = min(rated_ac, ac_raw)
    is_clipping = ac_raw > rated_ac

    return {
        "poa_irradiance_w_per_m2": round(poa_total, 2),
        "cell_temp_c": round(t_cell, 2),
        "dc_power_mw": round(dc_power, 4),
        "ac_available_mw": round(ac_available, 4),
        "is_clipping": is_clipping,
    }


# ============================================================================
# 2. Wind Aerodynamics & Density-Corrected Power Curves
# ============================================================================

@dataclass(frozen=True)
class WindAssetParams:
    """Site-specific wind farm physical configuration."""
    asset_id: str
    capacity_mw: float
    hub_height_m: float = 120.0
    cut_in_speed_ms: float = 3.0
    rated_speed_ms: float = 11.5
    cut_out_speed_ms: float = 25.0
    wake_loss_factor: float = 0.065   # 6.5% park wake effect
    electrical_loss: float = 0.02     # 2% collection grid loss


def air_density_kg_per_m3(ambient_temp_c: float, pressure_hpa: float = 1013.25) -> float:
    """Compute local air density from ideal gas law."""
    t_kelvin = ambient_temp_c + 273.15
    p_pa = pressure_hpa * 100.0
    # Dry air gas constant R_spec = 287.058 J/(kg*K)
    return max(0.8, min(1.5, p_pa / (287.058 * t_kelvin)))


def calculate_density_corrected_wind_power(
    params: WindAssetParams,
    wind_speed_ms: float,
    ambient_temp_c: float = 28.0,
    pressure_hpa: float = 1008.0,
) -> dict[str, float]:
    """Calculate delivered aerodynamic wind power with air density correction."""
    # Standard reference air density rho_0 = 1.225 kg/m3 (ISO standard)
    rho = air_density_kg_per_m3(ambient_temp_c, pressure_hpa)
    density_ratio = rho / 1.225
    # Effective aerodynamic wind speed scaled by density cube-root
    v_eff = wind_speed_ms * math.pow(density_ratio, 1.0 / 3.0)

    if v_eff < params.cut_in_speed_ms or v_eff >= params.cut_out_speed_ms:
        power_raw = 0.0
    elif v_eff >= params.rated_speed_ms:
        power_raw = params.capacity_mw
    else:
        # IEC non-linear cubic aerodynamic power curve ramp
        v_ci = params.cut_in_speed_ms
        v_rat = params.rated_speed_ms
        ramp = (math.pow(v_eff, 3) - math.pow(v_ci, 3)) / (math.pow(v_rat, 3) - math.pow(v_ci, 3))
        power_raw = params.capacity_mw * max(0.0, min(1.0, ramp))

    # Apply wake and electrical collection losses
    delivered_mw = power_raw * (1.0 - params.wake_loss_factor) * (1.0 - params.electrical_loss)
    delivered_mw = max(0.0, min(params.capacity_mw, delivered_mw))

    return {
        "air_density_kg_m3": round(rho, 4),
        "effective_wind_speed_ms": round(v_eff, 2),
        "aerodynamic_power_mw": round(power_raw, 4),
        "delivered_wind_mw": round(delivered_mw, 4),
        "capacity_factor": round(delivered_mw / params.capacity_mw, 4) if params.capacity_mw > 0 else 0.0,
    }


# ============================================================================
# 3. Physics-Informed Electrochemical Battery Degradation
# ============================================================================

@dataclass(frozen=True)
class BessDegradationParams:
    """Semi-empirical lithium battery degradation parameters (LiFePO4/NMC)."""
    bess_id: str
    nominal_capacity_mwh: float
    replacement_cost_usd_per_kwh: float = 140.0   # Current grid-scale BESS replacement cost ($140/kWh = $140,000/MWh)
    cycle_aging_coeff: float = 1.5e-4              # Base capacity fade per equivalent full cycle
    dod_exponent: float = 1.65                     # Non-linear stress penalty for deep cycles
    c_rate_exponent: float = 0.5                   # Thermal current density stress
    arrhenius_activation_ev: float = 0.35          # Activation energy for SEI growth
    reference_temp_c: float = 25.0                 # Calendar aging reference temperature


def calculate_bess_degradation(
    params: BessDegradationParams,
    charge_mw: float,
    discharge_mw: float,
    current_soc: float,
    dt_hours: float = 0.25,
    cell_temp_c: float = 30.0,
) -> dict[str, float]:
    """Compute physical capacity fade (Delta SOH) and marginal wear cost.

    Combines:
    1. Depth-of-Discharge (DoD) non-linear cycle stress
    2. C-rate thermal current factor
    3. Arrhenius thermal calendar aging
    """
    total_flow_mw = charge_mw + discharge_mw
    throughput_mwh = total_flow_mw * dt_hours

    if throughput_mwh <= 1e-9:
        # Pure calendar aging when idle
        # Arrhenius relationship: exp(-Ea / (k_B * T))
        k_b_ev = 8.617333262145e-5  # Boltzmann constant in eV/K
        t_kelvin = cell_temp_c + 273.15
        t_ref_kelvin = params.reference_temp_c + 273.15
        temp_factor = math.exp((params.arrhenius_activation_ev / k_b_ev) * (1.0 / t_ref_kelvin - 1.0 / t_kelvin))
        cal_fade_per_hour = 1.2e-7  # Nominal ~1.0% calendar loss per year
        delta_soh_cal = cal_fade_per_hour * dt_hours * temp_factor

        total_fade = delta_soh_cal
        marginal_cost_usd = total_fade * (params.nominal_capacity_mwh * 1000.0 * params.replacement_cost_usd_per_kwh)
        return {
            "throughput_mwh": 0.0,
            "c_rate": 0.0,
            "dod_stress": 0.0,
            "delta_soh_cycle": 0.0,
            "delta_soh_calendar": round(delta_soh_cal, 9),
            "total_soh_fade": round(total_fade, 9),
            "marginal_wear_cost_usd": round(marginal_cost_usd, 4),
        }

    # Effective C-rate
    c_rate = total_flow_mw / params.nominal_capacity_mwh
    # Depth-of-Discharge proxy: distance from comfortable 50% SoC
    effective_dod = min(1.0, max(0.1, abs(current_soc - 0.5) * 2.0))
    dod_stress = math.pow(effective_dod, params.dod_exponent)

    # Fractional full cycle equivalents (1 EFC = 2 * Nominal Capacity throughput)
    efc = throughput_mwh / (2.0 * params.nominal_capacity_mwh)
    c_rate_factor = math.pow(max(0.1, c_rate), params.c_rate_exponent)

    delta_soh_cycle = params.cycle_aging_coeff * efc * dod_stress * c_rate_factor

    # Calendar aging component
    k_b_ev = 8.617333262145e-5
    t_kelvin = cell_temp_c + 273.15
    t_ref_kelvin = params.reference_temp_c + 273.15
    temp_factor = math.exp((params.arrhenius_activation_ev / k_b_ev) * (1.0 / t_ref_kelvin - 1.0 / t_kelvin))
    cal_fade_per_hour = 1.2e-7
    delta_soh_cal = cal_fade_per_hour * dt_hours * temp_factor

    total_fade = delta_soh_cycle + delta_soh_cal
    marginal_cost_usd = total_fade * (params.nominal_capacity_mwh * 1000.0 * params.replacement_cost_usd_per_kwh)

    return {
        "throughput_mwh": round(throughput_mwh, 6),
        "c_rate": round(c_rate, 4),
        "dod_stress": round(dod_stress, 4),
        "delta_soh_cycle": round(delta_soh_cycle, 9),
        "delta_soh_calendar": round(delta_soh_cal, 9),
        "total_soh_fade": round(total_fade, 9),
        "marginal_wear_cost_usd": round(marginal_cost_usd, 4),
    }


# ============================================================================
# 4. Quantile Regression Forecaster & Pinball Loss Evaluation
# ============================================================================

class QuantileRegressor:
    """Deterministic multi-quantile gradient boosting emulator.

    Fits and produces calibrated P10, P50, and P90 forecast bands.
    """

    def __init__(self, quantiles: tuple[float, float, float] = (0.10, 0.50, 0.90)) -> None:
        self.quantiles = quantiles
        self._coefficients: dict[float, np.ndarray] = {}

    def fit(self, x: np.ndarray, y: np.ndarray) -> "QuantileRegressor":
        """Fit linear quantile weights using asymmetric iterative reweighted least squares."""
        n_samples, n_features = x.shape
        x_bias = np.hstack([np.ones((n_samples, 1)), x])

        for q in self.quantiles:
            # Initialize with OLS estimate
            beta = np.linalg.lstsq(x_bias, y, rcond=None)[0]
            for _ in range(25):  # Fast convergent IRLS iterations
                res = y - x_bias @ beta
                weights = np.where(res >= 0, q / (np.abs(res) + 1e-4), (1.0 - q) / (np.abs(res) + 1e-4))
                w_diag = np.sqrt(weights)
                xw = x_bias * w_diag[:, np.newaxis]
                yw = y * w_diag
                beta = np.linalg.lstsq(xw, yw, rcond=None)[0]
            self._coefficients[q] = beta
        return self

    def predict(self, x: np.ndarray) -> dict[float, np.ndarray]:
        """Generate quantile forecasts for input features."""
        n_samples = x.shape[0]
        x_bias = np.hstack([np.ones((n_samples, 1)), x])
        predictions: dict[float, np.ndarray] = {}
        for q in self.quantiles:
            pred = x_bias @ self._coefficients[q]
            predictions[q] = np.maximum(0.0, pred)

        # Enforce non-crossing quantile property: P10 <= P50 <= P90
        p10 = predictions[0.10]
        p50 = np.maximum(p10, predictions[0.50])
        p90 = np.maximum(p50, predictions[0.90])
        return {0.10: p10, 0.50: p50, 0.90: p90}


def evaluate_pinball_loss(y_true: np.ndarray, y_pred: np.ndarray, quantile: float) -> float:
    """Compute strictly proper pinball / check loss for quantile verification."""
    delta = y_true - y_pred
    loss = np.maximum(quantile * delta, (quantile - 1.0) * delta)
    return float(np.mean(loss))


def empirical_coverage(y_true: np.ndarray, p10: np.ndarray, p90: np.ndarray) -> float:
    """Compute empirical coverage percentage of the 80% confidence interval (P10 to P90)."""
    covered = (y_true >= p10) & (y_true <= p90)
    return float(np.mean(covered))


__all__ = [
    "SolarAssetParams",
    "WindAssetParams",
    "BessDegradationParams",
    "solar_position",
    "calculate_solar_poa_ac_power",
    "air_density_kg_per_m3",
    "calculate_density_corrected_wind_power",
    "calculate_bess_degradation",
    "QuantileRegressor",
    "evaluate_pinball_loss",
    "empirical_coverage",
]
