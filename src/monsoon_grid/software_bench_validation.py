"""Software bench validation: a pure-software droop and current-limit emulator.

This module makes no HIL, CHIL, or PHIL claim. There is no hardware, no firmware,
no ESP32, and no measured timing. Droop and current limiting are simulated
arithmetic over a declared electrical envelope. The numeric envelope values are a
declared configuration, not a measurement of any board.

Replaces the former ``hardware_validation.py`` (design B4). ``BenchDroopFrame``
is distinct from ``contracts.BenchTelemetry`` and from ``bench_mock.py`` (the
separate protocol mock); this module is the software droop emulator only.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .canonical import sha256_hex

_BENCH_EPOCH = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)


def _frame_timestamp(sequence: int) -> str:
    """Deterministic synthetic timestamp derived from the frame sequence.

    The emulator is pure software and is not tied to a replay clock; this is a
    reproducible label, not a measurement, and does not read the wall clock.
    """
    return (_BENCH_EPOCH + dt.timedelta(milliseconds=100 * sequence)).isoformat()


@dataclass(frozen=True)
class SoftwareBenchEnvelopeConfig:
    """Declared electrical envelope for the software bench emulator (configuration, not measurement)."""

    nominal_voltage_v: float = 12.0
    hard_current_limit_a: float = 1.0
    max_power_budget_w: float = 12.0
    min_safe_voltage_v: float = 10.5
    max_safe_voltage_v: float = 13.5
    watchdog_timeout_ms: int = 2000
    critical_load_w: float = 2.4
    flexible_load_a_w: float = 3.6
    flexible_load_b_w: float = 3.6


@dataclass(frozen=True)
class BenchDroopFrame:
    """A simulated telemetry frame from the software bench emulator."""

    frame_sequence: int
    timestamp_utc: str
    bus_voltage_v: float
    bus_current_a: float
    bus_power_w: float
    mini_pv_irradiance_lux: float
    relay_critical_enabled: bool
    relay_flex_a_enabled: bool
    relay_flex_b_enabled: bool
    bench_state: str  # READY, ACTIVE_NORMAL, OVERCURRENT_TRIP, WATCHDOG_TRIP, SAFE_DISCONNECTED
    failsafe_tripped: bool
    emulator_version: str = "software-bench-droop-emulator-v1"
    digest: str = field(default="")

    def compute_digest(self) -> str:
        payload = {
            "seq": self.frame_sequence,
            "v": self.bus_voltage_v,
            "i": self.bus_current_a,
            "p": self.bus_power_w,
            "state": self.bench_state,
            "tripped": self.failsafe_tripped,
        }
        return sha256_hex(payload)


class SoftwareBenchEmulator:
    """Pure-software droop and current-limit emulator (no hardware, no measurement)."""

    def __init__(self, config: SoftwareBenchEnvelopeConfig | None = None) -> None:
        self.config = config or SoftwareBenchEnvelopeConfig()
        self.sequence = 0
        self.state = "READY"
        self.relay_critical = True
        self.relay_flex_a = True
        self.relay_flex_b = True
        self.failsafe_tripped = False
        self.mini_pv_lux = 12500.0

    def step(
        self,
        cmd_flex_a_enable: bool = True,
        cmd_flex_b_enable: bool = True,
        ambient_lux: float = 12500.0,
        time_since_heartbeat_ms: int = 100,
    ) -> BenchDroopFrame:
        """Advance the emulator by one cycle and apply the simulated safety checks."""
        self.sequence += 1
        now_str = _frame_timestamp(self.sequence)
        self.mini_pv_lux = ambient_lux

        if time_since_heartbeat_ms > self.config.watchdog_timeout_ms:
            self.state = "WATCHDOG_TRIP"
            self.failsafe_tripped = True
            self.relay_flex_a = False
            self.relay_flex_b = False
            current_a = self.config.critical_load_w / self.config.nominal_voltage_v
            power_w = self.config.critical_load_w
            return self._frame(now_str, self.config.nominal_voltage_v, current_a, power_w, ambient_lux)

        self.relay_flex_a = cmd_flex_a_enable
        self.relay_flex_b = cmd_flex_b_enable
        active_power_w = self.config.critical_load_w
        if self.relay_flex_a:
            active_power_w += self.config.flexible_load_a_w
        if self.relay_flex_b:
            active_power_w += self.config.flexible_load_b_w

        bus_voltage = self.config.nominal_voltage_v - (active_power_w / 12.0) * 0.15
        total_current = active_power_w / max(1.0, bus_voltage)

        if total_current > self.config.hard_current_limit_a:
            self.state = "OVERCURRENT_TRIP"
            self.failsafe_tripped = True
            self.relay_flex_a = False
            self.relay_flex_b = False
            total_current = self.config.critical_load_w / self.config.nominal_voltage_v
            active_power_w = self.config.critical_load_w
        elif bus_voltage < self.config.min_safe_voltage_v or bus_voltage > self.config.max_safe_voltage_v:
            self.state = "VOLTAGE_OUT_OF_BOUNDS"
            self.failsafe_tripped = True
        else:
            self.state = "ACTIVE_NORMAL"
            self.failsafe_tripped = False

        return self._frame(now_str, round(bus_voltage, 2), round(total_current, 3), round(active_power_w, 2), ambient_lux)

    def manual_emergency_disconnect(self) -> BenchDroopFrame:
        """Simulated master disconnect: all relays open, safe state."""
        self.sequence += 1
        self.state = "SAFE_DISCONNECTED"
        self.failsafe_tripped = True
        self.relay_critical = False
        self.relay_flex_a = False
        self.relay_flex_b = False
        now_str = _frame_timestamp(self.sequence)
        return self._frame(now_str, 0.0, 0.0, 0.0, 0.0)

    def _frame(self, timestamp: str, voltage: float, current: float, power: float, lux: float) -> BenchDroopFrame:
        frame = BenchDroopFrame(
            frame_sequence=self.sequence,
            timestamp_utc=timestamp,
            bus_voltage_v=voltage,
            bus_current_a=current,
            bus_power_w=power,
            mini_pv_irradiance_lux=lux,
            relay_critical_enabled=self.relay_critical,
            relay_flex_a_enabled=self.relay_flex_a,
            relay_flex_b_enabled=self.relay_flex_b,
            bench_state=self.state,
            failsafe_tripped=self.failsafe_tripped,
        )
        object.__setattr__(frame, "digest", frame.compute_digest())
        return frame


__all__ = [
    "SoftwareBenchEnvelopeConfig",
    "BenchDroopFrame",
    "SoftwareBenchEmulator",
]
