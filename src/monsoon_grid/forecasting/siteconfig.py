"""Site / module / inverter / turbine configuration (design §4, §12, §17).

Every numeric field that is self-chosen is labelled ``scenario_assumption`` and
carries ``value``/``unit``/``source``/``label``. Config-load validation (§12)
raises :class:`ConfigError` on out-of-range geometry. Only ``tracking="fixed"``
is supported; anything else is rejected rather than silently approximated.
"""

from __future__ import annotations

from typing import Literal

from pydantic import field_validator, model_validator

from .errors import ConfigError
from .types import StrictRecord


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ConfigError(msg)


class PvModuleConfig(StrictRecord):
    """PV module electrical/thermal parameters (all assumptions; §17)."""

    gamma_p_per_degc: float = -0.0035  # power temp coeff, c-Si typical (assumption)
    thermal_model: Literal["sapm", "faiman", "noct"] = "sapm"
    # SAPM open-rack glass/cell/polymer-sheet row (SAND2004-3535); applicability assumed.
    sapm_a: float = -3.56
    sapm_b: float = -0.075
    sapm_delta_t: float = 3.0
    # Faiman (2008) coefficients, used only when thermal_model == "faiman".
    faiman_u0: float = 25.0
    faiman_u1: float = 6.84
    noct_celsius: float = 45.0

    @field_validator("gamma_p_per_degc")
    @classmethod
    def _check_gamma(cls, v: float) -> float:
        _require(-0.01 <= v <= 0.0, "gamma_p must be in [-0.01, 0.0] /degC")
        return v


class InverterConfig(StrictRecord):
    """Inverter sizing + part-load efficiency table (design §4.6)."""

    dc_ac_ratio: float = 1.20  # assumption, swept {1.10,1.20,1.35}
    # Non-monotone by design (terminal dip): PCHIP shape-preserving interp.
    part_load_x: list[float] = [0.00, 0.05, 0.10, 0.20, 0.50, 1.00]
    part_load_eta: list[float] = [0.000, 0.900, 0.950, 0.970, 0.985, 0.980]

    @model_validator(mode="after")
    def _check_table(self) -> "InverterConfig":
        _require(self.dc_ac_ratio > 0, "dc_ac_ratio must be > 0")
        _require(len(self.part_load_x) == len(self.part_load_eta), "part-load table length mismatch")
        _require(len(self.part_load_x) >= 2, "part-load table needs >= 2 knots")
        xs = self.part_load_x
        _require(all(xs[i] < xs[i + 1] for i in range(len(xs) - 1)), "part_load_x must be strictly increasing")
        _require(xs[0] == 0.0, "part_load_x must start at 0.0")
        _require(all(0.0 <= e <= 1.0 for e in self.part_load_eta), "efficiency must be in [0,1]")
        return self


class PvSiteConfig(StrictRecord):
    """A PV site: geometry + capacity + loss stack (design §4, §12)."""

    site_id: str
    latitude_deg: float
    longitude_deg: float
    elevation_m: float = 0.0
    tilt_deg: float
    azimuth_deg: float = 180.0  # south-facing (northern hemisphere), assumption
    tracking: Literal["fixed"] = "fixed"
    ac_capacity_mw: float
    albedo: float = 0.20  # assumption, swept {0.15,0.20,0.30}
    soiling_loss: float = 0.03  # assumption
    wiring_loss: float = 0.015  # assumption
    mismatch_loss: float = 0.02  # assumption
    scheduled_availability_factor: float = 0.97  # applied (§4.7)
    forced_outage_factor: float = 0.0  # DEFINED, NEVER APPLIED (§4.7, invariant 5)
    elevation_floor_deg: float = 0.0  # gate PV below this solar elevation
    physical_max_factor: float = 1.05  # clip bound = this * C_ac (§5.1)
    transposition_model: Literal["liu_jordan", "hay_davies"] = "liu_jordan"
    module: PvModuleConfig = PvModuleConfig()
    inverter: InverterConfig = InverterConfig()

    @model_validator(mode="after")
    def _validate(self) -> "PvSiteConfig":
        _require(-90.0 <= self.latitude_deg <= 90.0, "latitude must be in [-90, 90]")
        _require(-180.0 <= self.longitude_deg <= 180.0, "longitude must be in [-180, 180]")
        _require(-430.0 <= self.elevation_m <= 6000.0, "elevation must be in [-430, 6000] m")
        _require(0.0 <= self.tilt_deg <= 90.0, "tilt must be in [0, 90]")
        _require(0.0 <= self.azimuth_deg < 360.0, "azimuth must be in [0, 360)")
        _require(self.ac_capacity_mw > 0.0, "ac_capacity_mw must be > 0")
        _require(0.0 <= self.albedo <= 1.0, "albedo must be in [0, 1]")
        _require(self.physical_max_factor >= 1.0, "physical_max_factor must be >= 1")
        return self


class TurbineConfig(StrictRecord):
    """A wind turbine/farm config (design §4.8, §12)."""

    site_id: str
    hub_height_m: float = 120.0
    ac_capacity_mw: float = 1.0
    rotor_diameter_m: float = 110.0
    v_cut_in_ms: float = 3.0  # assumption
    v_rated_ms: float = 12.0  # assumption
    v_cut_out_ms: float = 25.0  # assumption
    v_cutout_restart_ms: float = 22.0  # v_co - 3 (assumption)
    cp_max: float = 0.45  # <= Betz 16/27 (assumption)
    wake_loss: float = 0.08  # assumption
    electrical_loss: float = 0.02  # assumption
    scheduled_availability_factor: float = 0.97  # applied
    forced_outage_factor: float = 0.0  # defined, never applied
    regulation: Literal["pitch", "stall"] = "pitch"
    shear_model: Literal["estimated_power_law", "fixed_power_law", "log_law"] = "estimated_power_law"
    fixed_shear_alpha: float = 0.143  # crude 1/7 power law (assumption)
    density_correction: Literal["iec", "none"] = "iec"
    curve_source: Literal["datasheet", "isotonic_conditional_mean", "parametric"] = "parametric"

    @model_validator(mode="after")
    def _validate(self) -> "TurbineConfig":
        _require(10.0 <= self.hub_height_m <= 250.0, "hub_height must be in [10, 250] m")
        _require(self.ac_capacity_mw > 0.0, "ac_capacity_mw must be > 0")
        _require(0.0 < self.v_cut_in_ms < self.v_rated_ms < self.v_cut_out_ms, "require 0 < v_ci < v_rated < v_co")
        _require(self.v_cutout_restart_ms <= self.v_cut_out_ms, "restart speed must be <= cut-out")
        _require(0.0 < self.cp_max < 16.0 / 27.0, "cp_max must be in (0, Betz)")
        return self


__all__ = [
    "PvModuleConfig",
    "InverterConfig",
    "PvSiteConfig",
    "TurbineConfig",
]
