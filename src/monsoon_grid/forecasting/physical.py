"""Part A -- physical available-power baselines (design §4).

Pure functions over numpy arrays. No fitted parameters derived from the target
variable, no hidden defaults -- every coefficient arrives through a frozen config
from :mod:`siteconfig`. The module produces **available** AC power only (reduced
by weather/soiling/thermal/electrical/scheduled-availability); never by
curtailment, dispatch or forced outage (§4.7, invariant 5). There is no
``delivered`` field anywhere here.

Citations (verify table/page in reading, per §21):
- Solar position: NOAA/Meeus formulation (Meeus 1998), with equation of time.
- Decomposition: Erbs, Klein & Duffie (1982).
- Transposition: isotropic Liu & Jordan (1960/61); Hay & Davies (1980) selectable.
- Cell temperature: SAPM (King, Boyson & Kratochvil 2004, SAND2004-3535);
  Faiman (2008); NOCT (Ross 1976) selectable.
- Wind density correction: IEC 61400-12-1. PCHIP: Fritsch & Carlson (1980).
No novelty is claimed for any of these; they are established methods.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.optimize import isotonic_regression

from .errors import ConfigError, PhysicalConsistencyError
from .siteconfig import InverterConfig, PvModuleConfig, PvSiteConfig, TurbineConfig

# --- physical constants (assumptions / standard values, §17) ----------------
I0_W_M2 = 1361.0  # total solar irradiance, WMO/CODATA class value
RHO0_KG_M3 = 1.225  # ISA sea-level air density
R_D = 287.058  # J/(kg K) dry air gas constant
R_V = 461.495  # J/(kg K) water vapour gas constant
G_ACCEL = 9.80665
MOLAR_MASS_AIR = 0.0289644  # kg/mol
R_UNIVERSAL = 8.31446
COS_85 = float(np.cos(np.radians(85.0)))


# ===========================================================================
# Solar geometry (design §4.2)
# ===========================================================================


def _julian_day(epoch_seconds: np.ndarray) -> np.ndarray:
    # Unix epoch 1970-01-01 00:00 UTC == JD 2440587.5
    return 2440587.5 + np.asarray(epoch_seconds, dtype=np.float64) / 86400.0


@dataclass(frozen=True)
class SolarPosition:
    zenith_deg: np.ndarray
    elevation_deg: np.ndarray
    azimuth_deg: np.ndarray
    declination_deg: np.ndarray
    eq_of_time_min: np.ndarray


def solar_position(epoch_seconds: np.ndarray, latitude_deg: float, longitude_deg: float) -> SolarPosition:
    """NOAA/Meeus solar position with the equation of time (§4.2).

    Accuracy class ~0.01 deg for 1950-2050. Refraction/parallax not applied; see
    the elevation floor in the PV chain for the horizon caveat.
    """
    jd = _julian_day(epoch_seconds)
    jc = (jd - 2451545.0) / 36525.0  # Julian century since J2000.0

    geom_mean_long = np.mod(280.46646 + jc * (36000.76983 + jc * 0.0003032), 360.0)
    geom_mean_anom = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    ecc = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)

    m_rad = np.radians(geom_mean_anom)
    sun_eq_center = (
        np.sin(m_rad) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
        + np.sin(2 * m_rad) * (0.019993 - 0.000101 * jc)
        + np.sin(3 * m_rad) * 0.000289
    )
    true_long = geom_mean_long + sun_eq_center
    omega = 125.04 - 1934.136 * jc
    app_long = true_long - 0.00569 - 0.00478 * np.sin(np.radians(omega))

    mean_obliq = 23.0 + (26.0 + ((21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813)))) / 60.0) / 60.0
    obliq_corr = mean_obliq + 0.00256 * np.cos(np.radians(omega))

    declination = np.degrees(np.arcsin(np.sin(np.radians(obliq_corr)) * np.sin(np.radians(app_long))))

    y = np.tan(np.radians(obliq_corr / 2.0)) ** 2
    eot = 4.0 * np.degrees(
        y * np.sin(2 * np.radians(geom_mean_long))
        - 2 * ecc * np.sin(m_rad)
        + 4 * ecc * y * np.sin(m_rad) * np.cos(2 * np.radians(geom_mean_long))
        - 0.5 * y * y * np.sin(4 * np.radians(geom_mean_long))
        - 1.25 * ecc * ecc * np.sin(2 * m_rad)
    )  # equation of time in minutes

    # True solar time (minutes) from UTC fractional day + longitude + EoT.
    frac_day = np.mod(np.asarray(epoch_seconds, dtype=np.float64), 86400.0) / 86400.0
    minutes_utc = frac_day * 1440.0
    true_solar_time = np.mod(minutes_utc + eot + 4.0 * longitude_deg, 1440.0)

    hour_angle = true_solar_time / 4.0 - 180.0  # degrees
    ha_rad = np.radians(hour_angle)
    lat_rad = np.radians(latitude_deg)
    dec_rad = np.radians(declination)

    cos_zenith = np.sin(lat_rad) * np.sin(dec_rad) + np.cos(lat_rad) * np.cos(dec_rad) * np.cos(ha_rad)
    cos_zenith = np.clip(cos_zenith, -1.0, 1.0)
    zenith = np.degrees(np.arccos(cos_zenith))
    elevation = 90.0 - zenith

    # Azimuth (degrees from north, clockwise).
    denom = np.cos(np.radians(zenith)) * np.cos(lat_rad)
    with np.errstate(invalid="ignore", divide="ignore"):
        cos_az = (np.sin(dec_rad) - np.sin(lat_rad) * cos_zenith) / np.where(np.abs(denom) < 1e-12, np.nan, denom)
    cos_az = np.clip(cos_az, -1.0, 1.0)
    az = np.degrees(np.arccos(cos_az))
    azimuth = np.where(hour_angle > 0.0, np.mod(az + 180.0, 360.0), np.mod(540.0 - az, 360.0))

    return SolarPosition(
        zenith_deg=zenith,
        elevation_deg=elevation,
        azimuth_deg=azimuth,
        declination_deg=declination,
        eq_of_time_min=eot,
    )


# ===========================================================================
# Clearness index + decomposition (design §4.3)
# ===========================================================================


def eccentricity_correction(epoch_seconds: np.ndarray) -> np.ndarray:
    """E0 = 1 + 0.033 cos(2 pi doy / 365) (Duffie & Beckman)."""
    secs = np.mod(np.asarray(epoch_seconds, dtype=np.float64), 365.25 * 86400.0)
    doy = secs / 86400.0
    return 1.0 + 0.033 * np.cos(2.0 * np.pi * doy / 365.0)


def clearness_index(ghi: np.ndarray, zenith_deg: np.ndarray, e0: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Clearness index kt and its validity mask (elevation >= 5 deg)."""
    cos_z = np.cos(np.radians(zenith_deg))
    denom = I0_W_M2 * e0 * np.maximum(cos_z, COS_85)
    kt = np.asarray(ghi, dtype=np.float64) / denom
    kt = np.clip(kt, 0.0, 1.2)
    kt_valid = (90.0 - zenith_deg) >= 5.0
    return kt, kt_valid


def erbs_decomposition(ghi: np.ndarray, kt: np.ndarray, zenith_deg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Erbs et al. (1982) diffuse fraction -> (DNI, DHI).

    Hourly correlation; applied to hourly data only. Returns DNI and DHI in W/m^2.
    """
    kt = np.clip(np.asarray(kt, dtype=np.float64), 0.0, 1.0)
    diffuse_fraction = np.where(
        kt <= 0.22,
        1.0 - 0.09 * kt,
        np.where(
            kt <= 0.80,
            0.9511 - 0.1604 * kt + 4.388 * kt**2 - 16.638 * kt**3 + 12.336 * kt**4,
            0.165,
        ),
    )
    ghi = np.asarray(ghi, dtype=np.float64)
    dhi = diffuse_fraction * ghi
    cos_z = np.maximum(np.cos(np.radians(zenith_deg)), COS_85)
    dni = (ghi - dhi) / cos_z
    e0_cap = I0_W_M2  # loose cap (no eccentricity here; conservative)
    dni = np.clip(dni, 0.0, e0_cap)
    return dni, dhi


# ===========================================================================
# Transposition (design §4.4)
# ===========================================================================


def _aoi_cos(zenith_deg, azimuth_deg, tilt_deg, surface_azimuth_deg):
    z = np.radians(zenith_deg)
    a = np.radians(azimuth_deg)
    b = np.radians(tilt_deg)
    sa = np.radians(surface_azimuth_deg)
    cos_aoi = np.cos(z) * np.cos(b) + np.sin(z) * np.sin(b) * np.cos(a - sa)
    return np.clip(cos_aoi, -1.0, 1.0)


def transpose_poa(
    dni, dhi, ghi, zenith_deg, azimuth_deg, tilt_deg, surface_azimuth_deg, albedo, model="liu_jordan"
):
    """Plane-of-array irradiance (W/m^2). Isotropic Liu-Jordan or Hay-Davies."""
    cos_aoi = _aoi_cos(zenith_deg, azimuth_deg, tilt_deg, surface_azimuth_deg)
    beam = np.asarray(dni, dtype=np.float64) * np.maximum(cos_aoi, 0.0)
    b = np.radians(tilt_deg)
    ground = np.asarray(ghi, dtype=np.float64) * albedo * (1.0 - np.cos(b)) / 2.0
    dhi = np.asarray(dhi, dtype=np.float64)
    if model == "liu_jordan":
        diffuse = dhi * (1.0 + np.cos(b)) / 2.0
    elif model == "hay_davies":
        cos_z = np.maximum(np.cos(np.radians(zenith_deg)), COS_85)
        extra = I0_W_M2
        ai = np.clip(dni * cos_z / extra, 0.0, 1.0)  # anisotropy index
        circumsolar = dhi * ai * np.maximum(cos_aoi, 0.0) / cos_z
        isotropic = dhi * (1.0 - ai) * (1.0 + np.cos(b)) / 2.0
        diffuse = isotropic + circumsolar
    else:  # pragma: no cover - guarded by config
        raise ConfigError(f"unknown transposition model {model!r}")
    return beam + diffuse + ground


# ===========================================================================
# Cell temperature (design §4.5)
# ===========================================================================


def cell_temperature(poa, t_air_c, wind_ms, module: PvModuleConfig) -> np.ndarray:
    poa = np.asarray(poa, dtype=np.float64)
    t_air_c = np.asarray(t_air_c, dtype=np.float64)
    wind_ms = np.asarray(wind_ms, dtype=np.float64)
    if module.thermal_model == "sapm":
        t_module = poa * np.exp(module.sapm_a + module.sapm_b * wind_ms) + t_air_c
        return t_module + (poa / 1000.0) * module.sapm_delta_t
    if module.thermal_model == "faiman":
        return t_air_c + poa / (module.faiman_u0 + module.faiman_u1 * wind_ms)
    # noct
    return t_air_c + (module.noct_celsius - 20.0) / 800.0 * poa


# ===========================================================================
# Inverter part-load curve (design §4.6)
# ===========================================================================


def _inverter_interp(inv: InverterConfig) -> PchipInterpolator:
    return PchipInterpolator(np.asarray(inv.part_load_x), np.asarray(inv.part_load_eta), extrapolate=False)


def inverter_efficiency(dc_fraction: np.ndarray, inv: InverterConfig) -> np.ndarray:
    """PCHIP over the (non-monotone) part-load table (§4.6).

    - eta(0) = 0 at exactly zero.
    - constant extrapolation from x=0.05 for 0 < x < 0.05.
    - constant at eta(1.0) above x = 1.0.
    """
    x = np.asarray(dc_fraction, dtype=np.float64)
    pchip = _inverter_interp(inv)
    first_knot = inv.part_load_x[1]  # 0.05
    eta_first = inv.part_load_eta[1]
    eta_last = inv.part_load_eta[-1]
    out = pchip(np.clip(x, first_knot, 1.0))
    out = np.where(x <= 0.0, 0.0, out)
    out = np.where((x > 0.0) & (x < first_knot), eta_first, out)
    out = np.where(x > 1.0, eta_last, out)
    return out


# ===========================================================================
# PV chain (design §4.1, §4.6, §4.7)
# ===========================================================================


@dataclass(frozen=True)
class PvAvailability:
    """Available AC power only. Reduced by weather/soiling/thermal/electrical and
    scheduled availability; NEVER by curtailment, dispatch or forced outage."""

    ac_available_mw: np.ndarray
    is_clipping: np.ndarray
    poa_w_m2: np.ndarray
    cell_temp_c: np.ndarray
    kt: np.ndarray
    kt_valid: np.ndarray
    consistency_counts: dict[str, int]


def pv_available_power(
    epoch_seconds: np.ndarray,
    ghi: np.ndarray,
    t_air_c: np.ndarray,
    wind_ms: np.ndarray,
    site: PvSiteConfig,
    *,
    dni: np.ndarray | None = None,
    dhi: np.ndarray | None = None,
    physical_clamp_threshold: float = 0.01,
) -> PvAvailability:
    """Full PV chain -> available AC power (MW)."""
    epoch_seconds = np.asarray(epoch_seconds, dtype=np.float64)
    ghi = np.asarray(ghi, dtype=np.float64)
    n = ghi.shape[0]
    counts = {
        "negative_irradiance": 0,
        "component_inconsistent": 0,
        "poa_implausible": 0,
        "thermal_inversion": 0,
    }

    neg = ghi < 0.0
    counts["negative_irradiance"] = int(np.sum(neg))
    ghi = np.maximum(ghi, 0.0)

    pos = solar_position(epoch_seconds, site.latitude_deg, site.longitude_deg)
    e0 = eccentricity_correction(epoch_seconds)
    kt, kt_valid = clearness_index(ghi, pos.zenith_deg, e0)

    if dni is None or dhi is None:
        dni_arr, dhi_arr = erbs_decomposition(ghi, kt, pos.zenith_deg)
    else:
        dni_arr = np.asarray(dni, dtype=np.float64)
        dhi_arr = np.asarray(dhi, dtype=np.float64)

    cos_z = np.cos(np.radians(pos.zenith_deg))
    counts["component_inconsistent"] = int(np.sum((dni_arr * cos_z) > (ghi * 1.05)))

    poa = transpose_poa(
        dni_arr, dhi_arr, ghi, pos.zenith_deg, pos.azimuth_deg,
        site.tilt_deg, site.azimuth_deg, site.albedo, site.transposition_model,
    )
    poa = np.maximum(poa, 0.0)
    counts["poa_implausible"] = int(np.sum(poa > 1.5 * I0_W_M2 * e0))

    t_cell = cell_temperature(poa, t_air_c, wind_ms, site.module)
    counts["thermal_inversion"] = int(np.sum((t_cell < np.asarray(t_air_c)) & (poa > 0.0)))

    # DC power.
    p_ac_rating = site.ac_capacity_mw
    p_dc_stc = p_ac_rating * site.inverter.dc_ac_ratio
    loss = (1.0 - site.soiling_loss) * (1.0 - site.wiring_loss) * (1.0 - site.mismatch_loss)
    temp_factor = 1.0 + site.module.gamma_p_per_degc * (t_cell - 25.0)
    p_dc = p_dc_stc * (poa / 1000.0) * temp_factor * loss
    p_dc = np.maximum(p_dc, 0.0)

    dc_fraction = p_dc / p_dc_stc
    eta = inverter_efficiency(dc_fraction, site.inverter)
    p_ac = np.minimum(p_ac_rating, eta * p_dc)
    is_clipping = (eta * p_dc) >= p_ac_rating

    # Gate below the elevation floor and at night.
    off = pos.elevation_deg <= site.elevation_floor_deg
    p_ac = np.where(off, 0.0, p_ac)
    is_clipping = np.where(off, False, is_clipping)

    # Scheduled availability (applied). Forced outage NEVER applied.
    p_ac = p_ac * site.scheduled_availability_factor

    # Consistency guard.
    for name, c in counts.items():
        if n and (c / n) > physical_clamp_threshold:
            raise PhysicalConsistencyError(f"{name} count {c}/{n} exceeds {physical_clamp_threshold}")

    return PvAvailability(
        ac_available_mw=p_ac,
        is_clipping=np.asarray(is_clipping, dtype=bool),
        poa_w_m2=poa,
        cell_temp_c=t_cell,
        kt=kt,
        kt_valid=kt_valid,
        consistency_counts=counts,
    )


# ===========================================================================
# Wind: density, shear, power curve (design §4.8)
# ===========================================================================


def saturation_vapour_pressure_pa(t_c: np.ndarray) -> np.ndarray:
    """Magnus/Tetens saturation vapour pressure (Pa)."""
    t_c = np.asarray(t_c, dtype=np.float64)
    return 610.94 * np.exp(17.625 * t_c / (t_c + 243.04))


def humid_air_density(t_c, pressure_hpa, rh_pct=None) -> np.ndarray:
    """Humid-air density (kg/m^3) from partial pressures (§4.8)."""
    t_k = np.asarray(t_c, dtype=np.float64) + 273.15
    p_pa = np.asarray(pressure_hpa, dtype=np.float64) * 100.0
    if rh_pct is None:
        return p_pa / (R_D * t_k)
    p_v = np.clip(np.asarray(rh_pct, dtype=np.float64) / 100.0, 0.0, 1.0) * saturation_vapour_pressure_pa(t_c)
    p_d = p_pa - p_v
    return p_d / (R_D * t_k) + p_v / (R_V * t_k)


def shear_extrapolate(v_low, z_low, v_high, z_high, z_target) -> np.ndarray:
    """Locally-estimated power-law shear exponent between two measured levels."""
    v_low = np.asarray(v_low, dtype=np.float64)
    v_high = np.asarray(v_high, dtype=np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        alpha = np.log(np.maximum(v_high, 1e-6) / np.maximum(v_low, 1e-6)) / np.log(z_high / z_low)
    alpha = np.where(np.isfinite(alpha), alpha, 0.143)
    return v_low * (z_target / z_low) ** alpha


def shear_fixed(v_ref, z_ref, z_target, alpha) -> np.ndarray:
    return np.asarray(v_ref, dtype=np.float64) * (z_target / z_ref) ** alpha


def iec_density_correct_speed(v, rho, rho0=RHO0_KG_M3) -> np.ndarray:
    """Stall-regulation IEC 61400-12-1 effective-speed correction."""
    return np.asarray(v, dtype=np.float64) * (np.asarray(rho, dtype=np.float64) / rho0) ** (1.0 / 3.0)


def estimate_power_curve_isotonic(
    speed_ms: np.ndarray, normalized_power: np.ndarray, bin_width_ms: float = 0.5
) -> tuple[np.ndarray, np.ndarray]:
    """Isotonic conditional-mean power curve from (speed, normalized power).

    Returns (bin_centers, monotone normalized power). Used for the degraded
    GEFCom path on UNCORRECTED 100 m speed (§4.8). No density normalization.
    """
    speed_ms = np.asarray(speed_ms, dtype=np.float64)
    normalized_power = np.asarray(normalized_power, dtype=np.float64)
    finite = np.isfinite(speed_ms) & np.isfinite(normalized_power)
    speed_ms = speed_ms[finite]
    normalized_power = normalized_power[finite]
    if speed_ms.shape[0] == 0:
        raise ConfigError("cannot estimate a power curve from zero rows")
    v_max = float(np.max(speed_ms))
    edges = np.arange(0.0, v_max + bin_width_ms, bin_width_ms)
    centers = 0.5 * (edges[:-1] + edges[1:])
    idx = np.clip(np.digitize(speed_ms, edges) - 1, 0, len(centers) - 1)
    means = np.full(len(centers), np.nan)
    for b in range(len(centers)):
        sel = idx == b
        if np.any(sel):
            means[b] = float(np.mean(normalized_power[sel]))
    valid = np.isfinite(means)
    centers_v = centers[valid]
    means_v = means[valid]
    iso = isotonic_regression(means_v, increasing=True)
    return centers_v, np.asarray(iso.x, dtype=np.float64)


def parametric_power_curve(v, turbine: TurbineConfig) -> np.ndarray:
    """Cp-capped cubic parametric curve, normalized to [0,1] (§4.8 item 3)."""
    v = np.asarray(v, dtype=np.float64)
    v_ci, v_r, v_co = turbine.v_cut_in_ms, turbine.v_rated_ms, turbine.v_cut_out_ms
    # Normalized so that it reaches 1.0 at rated.
    ramp = ((v - v_ci) / (v_r - v_ci)) ** 3
    out = np.where(v < v_ci, 0.0, np.where(v < v_r, ramp, np.where(v < v_co, 1.0, 0.0)))
    return np.clip(out, 0.0, 1.0)


# ===========================================================================
# Jensen entry points (design §4.9) -- three distinct forms, no middle ground
# ===========================================================================


def power_from_speed(v, turbine: TurbineConfig, *, hysteresis: bool = False) -> np.ndarray:
    """Elementwise curve over any array (normalized [0,1] * capacity -> MW).

    The default, and the only form used in the Jensen identity test. Hysteresis
    is not expressible here; ``hysteresis=True`` raises to make the refusal
    explicit.
    """
    if hysteresis:
        raise ConfigError("power_from_speed is elementwise; use power_from_speed_sequence for cut-out latch")
    norm = parametric_power_curve(v, turbine)
    gain = (1.0 - turbine.wake_loss) * (1.0 - turbine.electrical_loss) * turbine.scheduled_availability_factor
    return norm * turbine.ac_capacity_mw * gain


def power_from_speed_sequence(v_series, turbine: TurbineConfig) -> tuple[np.ndarray, np.ndarray]:
    """Sequential scan along one time axis with a cut-out latch (§4.9).

    Returns (power_mw, cutout_state). Once speed exceeds v_co the turbine latches
    off and only resumes when speed falls below v_cutout_restart.
    """
    v_series = np.asarray(v_series, dtype=np.float64)
    gain = (1.0 - turbine.wake_loss) * (1.0 - turbine.electrical_loss) * turbine.scheduled_availability_factor
    power = np.zeros_like(v_series)
    state = np.zeros_like(v_series, dtype=bool)  # True == latched out
    latched = False
    for i, v in enumerate(v_series):
        if latched:
            if v < turbine.v_cutout_restart_ms:
                latched = False
        elif v >= turbine.v_cut_out_ms:
            latched = True
        state[i] = latched
        if latched:
            power[i] = 0.0
        else:
            power[i] = float(parametric_power_curve(np.array([v]), turbine)[0]) * turbine.ac_capacity_mw * gain
    return power, state


def expected_power_from_speed_samples(v_samples, turbine: TurbineConfig) -> np.ndarray:
    """Apply the sequential form per member along the time axis (§4.9).

    ``v_samples`` is (n_scenarios, n_times); each member gets its own latch.
    Returns (n_scenarios, n_times) power in MW. The only correct way to get an
    expectation or scenario quantiles through the nonlinear, path-dependent curve.
    """
    v_samples = np.asarray(v_samples, dtype=np.float64)
    out = np.zeros_like(v_samples)
    for s in range(v_samples.shape[0]):
        out[s], _ = power_from_speed_sequence(v_samples[s], turbine)
    return out


@dataclass(frozen=True)
class WindAvailability:
    ac_available_mw: np.ndarray
    density_kg_m3: np.ndarray
    density_correction: str


def wind_available_power(
    hub_speed_ms: np.ndarray,
    turbine: TurbineConfig,
    *,
    t_air_c: np.ndarray | None = None,
    pressure_hpa: np.ndarray | None = None,
    rh_pct: np.ndarray | None = None,
) -> WindAvailability:
    """Wind chain -> available AC power (MW), elementwise (no latch).

    Density correction applied only when inputs exist; otherwise
    ``density_correction="none"`` is recorded, never guessed (§4.8).
    """
    hub_speed_ms = np.asarray(hub_speed_ms, dtype=np.float64)
    if turbine.density_correction == "iec" and t_air_c is not None and pressure_hpa is not None:
        rho = humid_air_density(t_air_c, pressure_hpa, rh_pct)
        correction = "iec"
        if turbine.regulation == "stall":
            v_eff = iec_density_correct_speed(hub_speed_ms, rho)
            power = power_from_speed(v_eff, turbine)
        else:  # pitch: scale power below rated by density ratio
            power = power_from_speed(hub_speed_ms, turbine) * (rho / RHO0_KG_M3)
            power = np.minimum(power, turbine.ac_capacity_mw)
    else:
        rho = np.full(hub_speed_ms.shape, RHO0_KG_M3)
        correction = "none"
        power = power_from_speed(hub_speed_ms, turbine)
    return WindAvailability(ac_available_mw=power, density_kg_m3=rho, density_correction=correction)


__all__ = [
    "SolarPosition",
    "solar_position",
    "eccentricity_correction",
    "clearness_index",
    "erbs_decomposition",
    "transpose_poa",
    "cell_temperature",
    "inverter_efficiency",
    "PvAvailability",
    "pv_available_power",
    "saturation_vapour_pressure_pa",
    "humid_air_density",
    "shear_extrapolate",
    "shear_fixed",
    "iec_density_correct_speed",
    "estimate_power_curve_isotonic",
    "parametric_power_curve",
    "power_from_speed",
    "power_from_speed_sequence",
    "expected_power_from_speed_samples",
    "WindAvailability",
    "wind_available_power",
    "I0_W_M2",
    "RHO0_KG_M3",
]
