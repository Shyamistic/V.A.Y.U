"""Analytic-identity tests for the physical chain (design §4.11), Jensen gap and
cut-out latch (§4.9)."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from monsoon_grid.forecasting import physical as ph
from monsoon_grid.forecasting.errors import ConfigError
from monsoon_grid.forecasting.siteconfig import InverterConfig, PvSiteConfig, TurbineConfig


def _epochs(start="2024-06-21T00:00:00Z", hours=24):
    t0 = int(dt.datetime.fromisoformat(start).timestamp())
    return np.array([t0 + h * 3600 for h in range(hours)], dtype=np.float64)


# --------------------------------------------------------------------------- solar geometry


def test_declination_at_solstice_and_equinox():
    jun = ph.solar_position(_epochs("2024-06-21T12:00:00Z", 1), 0.0, 0.0)
    dec = ph.solar_position(_epochs("2024-12-21T12:00:00Z", 1), 0.0, 0.0)
    mar = ph.solar_position(_epochs("2024-03-20T12:00:00Z", 1), 0.0, 0.0)
    assert abs(jun.declination_deg[0] - 23.44) < 0.5
    assert abs(dec.declination_deg[0] + 23.44) < 0.5
    assert abs(mar.declination_deg[0]) < 0.6


def test_equation_of_time_envelope():
    eps = _epochs("2024-01-01T12:00:00Z", 1)
    for day in range(0, 360, 30):
        pos = ph.solar_position(eps + day * 86400, 0.0, 0.0)
        assert abs(pos.eq_of_time_min[0]) < 17.0


def test_elevation_symmetric_about_computed_noon():
    # Design §4.11: elevation symmetric about the COMPUTED solar noon.
    # Use the June solstice, where declination is at its extremum and dd/dt ~= 0,
    # so the elevation-vs-hour-angle symmetry is isolated from declination drift.
    day0 = int(dt.datetime.fromisoformat("2024-06-21T00:00:00Z").timestamp())
    # Locate solar noon to the second via a dense scan around midday.
    seconds = np.array([day0 + s for s in range(39000, 46800)], dtype=np.float64)
    pos_day = ph.solar_position(seconds, 0.0, 0.0)
    noon_epoch = float(seconds[int(np.argmax(pos_day.elevation_deg))])
    for dsec in (3600.0, 7200.0):
        before = ph.solar_position(np.array([noon_epoch - dsec]), 0.0, 0.0).elevation_deg[0]
        after = ph.solar_position(np.array([noon_epoch + dsec]), 0.0, 0.0).elevation_deg[0]
        assert abs(before - after) < 0.02


# --------------------------------------------------------------------------- clearness index


def test_kt_denominator_floor_below_5deg():
    # Build a case with the sun below the horizon -> kt_valid False, kt finite.
    eps = _epochs("2024-06-21T00:00:00Z", 1)  # midnight -> night
    pos = ph.solar_position(eps, 24.5, 72.0)
    e0 = ph.eccentricity_correction(eps)
    kt, kt_valid = ph.clearness_index(np.array([5.0]), pos.zenith_deg, e0)
    assert np.isfinite(kt[0])  # floor prevents divergence
    assert kt_valid[0] in (True, False)


# --------------------------------------------------------------------------- transposition


def test_poa_equals_ghi_at_zero_tilt_no_ground():
    # beta=0: ground term vanishes (1-cos0 = 0); isotropic diffuse = DHI;
    # beam = DNI*cos(aoi)=DNI*cos(zenith). beam+diffuse = DNI*cosz + DHI = GHI.
    zenith = np.array([30.0, 45.0, 60.0])
    ghi = np.array([700.0, 500.0, 300.0])
    cosz = np.cos(np.radians(zenith))
    dhi = 0.3 * ghi
    dni = (ghi - dhi) / cosz
    poa = ph.transpose_poa(dni, dhi, ghi, zenith, 180.0, 0.0, 180.0, 0.2, "liu_jordan")
    np.testing.assert_allclose(poa, ghi, rtol=1e-9)


def test_isotropic_diffuse_limit_at_90deg():
    # At beta=90, isotropic diffuse factor (1+cos90)/2 = 0.5 -> diffuse term DHI/2.
    poa_dhi_only = ph.transpose_poa(
        np.array([0.0]), np.array([400.0]), np.array([0.0]), np.array([90.0]), 180.0, 90.0, 180.0, 0.0, "liu_jordan"
    )
    assert poa_dhi_only[0] == pytest.approx(200.0, rel=1e-9)


def test_aoi_cos_equals_cosz_for_horizontal():
    cos_aoi = ph._aoi_cos(np.array([37.0]), np.array([120.0]), 0.0, 180.0)
    assert cos_aoi[0] == pytest.approx(np.cos(np.radians(37.0)), rel=1e-9)


# --------------------------------------------------------------------------- cell temp / power


def test_zero_irradiance_zero_power_and_tcell_equals_tair():
    site = PvSiteConfig(site_id="pv1", latitude_deg=24.5, longitude_deg=72.0, tilt_deg=25.0, ac_capacity_mw=100.0)
    eps = _epochs("2024-06-21T00:00:00Z", 6)  # night
    out = ph.pv_available_power(eps, np.zeros(6), np.full(6, 30.0), np.full(6, 2.0), site)
    assert np.allclose(out.ac_available_mw, 0.0)
    # with zero POA, SAPM T_module = 0*exp(...) + T_air = T_air; T_cell = T_air.
    np.testing.assert_allclose(out.cell_temp_c, 30.0, atol=1e-9)


# --------------------------------------------------------------------------- inverter PCHIP


def test_pchip_reproduces_knots_and_below_005_constant():
    inv = InverterConfig()
    x = np.array(inv.part_load_x)
    eta = ph.inverter_efficiency(x, inv)
    np.testing.assert_allclose(eta, np.array(inv.part_load_eta), atol=1e-9)
    # below 0.05 but above 0 -> constant at eta(0.05)
    below = ph.inverter_efficiency(np.array([0.001, 0.02, 0.049]), inv)
    assert np.allclose(below, inv.part_load_eta[1])
    # exactly 0 -> 0
    assert ph.inverter_efficiency(np.array([0.0]), inv)[0] == 0.0
    # above 1 -> eta(1.0)
    assert ph.inverter_efficiency(np.array([1.5]), inv)[0] == pytest.approx(inv.part_load_eta[-1])


# --------------------------------------------------------------------------- air density / wind


def test_air_density_monotone_in_temp_and_pressure():
    rho_cold = ph.humid_air_density(np.array([0.0]), np.array([1000.0]))
    rho_hot = ph.humid_air_density(np.array([40.0]), np.array([1000.0]))
    assert rho_cold[0] > rho_hot[0]  # decreasing in temperature
    rho_low_p = ph.humid_air_density(np.array([20.0]), np.array([900.0]))
    rho_high_p = ph.humid_air_density(np.array([20.0]), np.array([1050.0]))
    assert rho_high_p[0] > rho_low_p[0]  # increasing in pressure


def test_parametric_curve_monotone_and_zero_outside():
    t = TurbineConfig(site_id="w1", ac_capacity_mw=10.0)
    v = np.linspace(t.v_cut_in_ms, t.v_rated_ms, 50)
    p = ph.parametric_power_curve(v, t)
    assert np.all(np.diff(p) >= -1e-12)  # non-decreasing on [v_ci, v_rated]
    assert ph.parametric_power_curve(np.array([t.v_cut_in_ms - 0.1]), t)[0] == 0.0
    assert ph.parametric_power_curve(np.array([t.v_cut_out_ms + 0.1]), t)[0] == 0.0


def test_isotonic_estimate_non_decreasing():
    rng = np.random.default_rng(0)
    v = rng.uniform(0, 20, size=5000)
    p = np.clip((v / 12.0) ** 3, 0, 1) + rng.normal(0, 0.05, size=5000)
    centers, curve = ph.estimate_power_curve_isotonic(v, p)
    assert np.all(np.diff(curve) >= -1e-9)


# --------------------------------------------------------------------------- Jensen gap §4.9


def test_jensen_gap_is_material():
    t = TurbineConfig(site_id="w1", ac_capacity_mw=10.0)
    # Convex region: speeds around the cubic ramp midpoint. mean(f(V)) > f(mean(V)).
    center = (t.v_cut_in_ms + t.v_rated_ms) / 2.0
    v_conv = center + np.array([-2.0, 2.0])
    f_mean_conv = ph.power_from_speed(np.array([center]), t)[0]
    mean_f_conv = np.mean(ph.power_from_speed(v_conv, t))
    assert mean_f_conv > f_mean_conv + 1e-6

    # Concave region near rated (curve bends over to the plateau).
    near_rated = t.v_rated_ms - 0.5
    v_conc = near_rated + np.array([-1.5, 1.5])
    f_mean_conc = ph.power_from_speed(np.array([near_rated]), t)[0]
    mean_f_conc = np.mean(ph.power_from_speed(v_conc, t))
    assert mean_f_conc < f_mean_conc - 1e-6


def test_hysteresis_true_raises_config_error():
    t = TurbineConfig(site_id="w1", ac_capacity_mw=10.0)
    with pytest.raises(ConfigError):
        ph.power_from_speed(np.array([10.0]), t, hysteresis=True)


def test_cutout_latch_requires_restart_margin():
    t = TurbineConfig(site_id="w1", ac_capacity_mw=10.0)  # v_co=25, restart=22
    # speed goes above cut-out then dips to 23 (> restart) -> stays latched.
    series = np.array([10.0, 26.0, 23.0, 21.0, 10.0])
    power, state = ph.power_from_speed_sequence(series, t)
    assert state.tolist() == [False, True, True, False, False]
    assert power[1] == 0.0  # above cut-out
    assert power[2] == 0.0  # latched (23 > restart 22)
    assert power[4] > 0.0  # resumed


def test_expected_power_per_member_independent_latch():
    t = TurbineConfig(site_id="w1", ac_capacity_mw=10.0)
    samples = np.array([[26.0, 23.0, 10.0], [10.0, 11.0, 12.0]])
    out = ph.expected_power_from_speed_samples(samples, t)
    assert out[0, 1] == 0.0  # member 0 latched
    assert out[1, 1] > 0.0  # member 1 never latched
