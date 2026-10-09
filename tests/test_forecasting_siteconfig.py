"""Tests for siteconfig validation and codeid stability (design §12, §8.11)."""

from __future__ import annotations

import pytest

from monsoon_grid.forecasting.codeid import code_version, digest_tree
from monsoon_grid.forecasting.errors import ConfigError
from monsoon_grid.forecasting.siteconfig import InverterConfig, PvSiteConfig, TurbineConfig


def _pv(**over):
    base = dict(site_id="pv1", latitude_deg=24.5, longitude_deg=72.0, tilt_deg=25.0, ac_capacity_mw=150.0)
    base.update(over)
    return PvSiteConfig(**base)


def test_pv_valid():
    pv = _pv()
    assert pv.tracking == "fixed"
    assert pv.forced_outage_factor == 0.0  # defined, never applied


@pytest.mark.parametrize(
    "over",
    [
        dict(latitude_deg=120.0),
        dict(longitude_deg=-200.0),
        dict(elevation_m=9000.0),
        dict(tilt_deg=120.0),
        dict(azimuth_deg=360.0),
        dict(ac_capacity_mw=0.0),
    ],
)
def test_pv_out_of_range_raises_config_error(over):
    with pytest.raises(ConfigError):
        _pv(**over)


def test_pv_tracking_only_fixed():
    with pytest.raises(Exception):
        _pv(tracking="single_axis")


def test_turbine_valid_and_ordering():
    t = TurbineConfig(site_id="w1", ac_capacity_mw=50.0)
    assert t.v_cut_in_ms < t.v_rated_ms < t.v_cut_out_ms


@pytest.mark.parametrize(
    "over",
    [
        dict(hub_height_m=5.0),
        dict(v_cut_in_ms=15.0),  # breaks ordering v_ci < v_rated
        dict(cp_max=0.7),  # above Betz
    ],
)
def test_turbine_invalid(over):
    base = dict(site_id="w1", ac_capacity_mw=50.0)
    base.update(over)
    with pytest.raises(ConfigError):
        TurbineConfig(**base)


def test_inverter_part_load_table_validation():
    with pytest.raises(ConfigError):
        InverterConfig(part_load_x=[0.0, 0.1], part_load_eta=[0.9])  # length mismatch
    with pytest.raises(ConfigError):
        InverterConfig(part_load_x=[0.1, 0.2], part_load_eta=[0.9, 0.95])  # must start at 0


def test_code_version_stable_and_sensitive(tmp_path):
    # Build a tiny fake tree, digest, then edit and confirm change.
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")
    d1 = digest_tree(tmp_path, "**/*.py")
    d1_again = digest_tree(tmp_path, "**/*.py")
    assert d1 == d1_again  # stable under no-op
    (tmp_path / "b.py").write_text("y = 3\n")
    d2 = digest_tree(tmp_path, "**/*.py")
    assert d1 != d2  # changes under a real edit


def test_code_version_of_package_is_hex():
    cv = code_version()
    assert len(cv) == 64
    assert all(c in "0123456789abcdef" for c in cv)
