import datetime as dt

from monsoon_grid.contracts import Provenance, TelemetryRecord
from monsoon_grid.fixtures import load_demo_portfolio
from monsoon_grid.quality import evaluate_quality, replay_time


def test_quality_uses_replay_clock_and_noncritical_fallback():
    portfolio = load_demo_portfolio()
    now = replay_time(portfolio.fixture_start_utc, 40)
    record = TelemetryRecord(telemetry_id="aux-40", asset_id="deferrable_aux", signal="power_mw", value=9.0, unit="MW", observed_at=now - dt.timedelta(hours=2), received_at=now, quality="fresh", provenance=Provenance(label="scenario_assumption", source_id="test", source_version="1", input_refs=[]))
    result = evaluate_quality(portfolio, replay_id="r", slot_index=40, telemetry=[record], signals=["flexible_noncritical_load"])
    assert result.use_fallback
    assert result.decisions[0].code == "DEGRADED_QUALITY"
    assert not result.blocks_action


def test_future_observation_rejected():
    portfolio = load_demo_portfolio()
    now = replay_time(portfolio.fixture_start_utc, 1)
    record = TelemetryRecord(telemetry_id="solar-1", asset_id="solar-01", signal="power_mw", value=1.0, unit="MW", observed_at=now + dt.timedelta(minutes=1), received_at=now, quality="fresh", provenance=Provenance(label="scenario_assumption", source_id="test", source_version="1", input_refs=[]))
    result = evaluate_quality(portfolio, replay_id="r", slot_index=1, telemetry=[record], signals=["solar_availability"])
    assert result.decisions[0].code == "FUTURE_OBSERVATION_REJECTED"
