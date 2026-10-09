"""B4: software bench droop/current-limit emulator (no hardware, no measurement)."""

from __future__ import annotations

from monsoon_grid.software_bench_validation import (
    BenchDroopFrame,
    SoftwareBenchEmulator,
    SoftwareBenchEnvelopeConfig,
)


def test_software_bench_normal_operation() -> None:
    emulator = SoftwareBenchEmulator()
    frame = emulator.step(cmd_flex_a_enable=True, cmd_flex_b_enable=False, ambient_lux=15000.0)

    assert isinstance(frame, BenchDroopFrame)
    assert frame.bench_state == "ACTIVE_NORMAL"
    assert not frame.failsafe_tripped
    assert frame.bus_current_a <= 1.0  # within the declared 1 A envelope
    assert frame.relay_critical_enabled is True
    assert frame.relay_flex_a_enabled is True
    assert frame.relay_flex_b_enabled is False
    assert frame.digest != ""


def test_software_bench_watchdog_timeout_trip() -> None:
    emulator = SoftwareBenchEmulator()
    # Stale heartbeat (2500ms > 2000ms watchdog limit) drives the safe state.
    frame = emulator.step(time_since_heartbeat_ms=2500)

    assert frame.bench_state == "WATCHDOG_TRIP"
    assert frame.failsafe_tripped is True
    assert frame.relay_flex_a_enabled is False
    assert frame.relay_flex_b_enabled is False
    assert frame.relay_critical_enabled is True


def test_software_bench_master_disconnect() -> None:
    emulator = SoftwareBenchEmulator()
    frame = emulator.manual_emergency_disconnect()

    assert frame.bench_state == "SAFE_DISCONNECTED"
    assert frame.failsafe_tripped is True
    assert frame.bus_voltage_v == 0.0
    assert frame.bus_current_a == 0.0
    assert frame.relay_critical_enabled is False


def test_envelope_config_defaults() -> None:
    config = SoftwareBenchEnvelopeConfig()
    assert config.hard_current_limit_a == 1.0
    assert config.nominal_voltage_v == 12.0
