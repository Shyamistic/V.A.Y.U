"""Unit tests for multimodal ingestion: brightness-centroid nowcasting and parser."""

from __future__ import annotations

from monsoon_grid.multimodal import (
    DocumentExtractorEngine,
    SkyCameraFrame,
    VisionNowcastEngine,
)


def test_vision_nowcast_optical_flow() -> None:
    engine = VisionNowcastEngine()

    # Frame 0: cloud at center (32, 32)
    grid0 = [0.1] * (64 * 64)
    for y in range(28, 36):
        for x in range(28, 36):
            grid0[y * 64 + x] = 0.85

    # Frame 1: cloud moved to (36, 36) in 60 seconds
    grid1 = [0.1] * (64 * 64)
    for y in range(32, 40):
        for x in range(32, 40):
            grid1[y * 64 + x] = 0.85

    f0 = SkyCameraFrame(timestamp_utc="2026-07-01T10:00:00Z", resolution=(64, 64), raw_pixels=grid0)
    f1 = SkyCameraFrame(timestamp_utc="2026-07-01T10:01:00Z", resolution=(64, 64), raw_pixels=grid1)

    vx, vy = engine.compute_optical_flow_velocity(f0, f1, time_delta_seconds=60.0)
    assert vx > 0.0
    assert vy > 0.0

    nowcast = engine.project_solar_attenuation(f1, (vx, vy), 100.0, lead_time_minutes=15.0)
    assert 0.0 <= nowcast.match_strength <= 1.0
    assert nowcast.match_strength > 0.0  # cloud present and non-zero velocity
    assert "solar-05" in nowcast.affected_solar_assets
    assert nowcast.predicted_ramp_rate_mw_min >= 0.0


def test_document_extractor_storm_bulletin() -> None:
    extractor = DocumentExtractorEngine()
    imd_text = """
    INDIA METEOROLOGICAL DEPARTMENT (IMD) SPECIAL WEATHER BULLETIN
    SEVERE CYCLONIC SQUALL WARNING FOR REGION NORTH-WEST
    GALE WIND SPEED EXCEEDING 28 M/S EXPECTED FROM 09:00 TO 15:00 UTC.
    ALL RENEWABLE WIND GENERATION ASSETS MUST ENFORCE CUT-OUT PROTECTION.
    """
    directives = extractor.parse_document(imd_text, source_label="imd_bulletin_2026_07.pdf")
    assert len(directives) == 1
    d = directives[0]
    assert d.directive_type == "STORM_DERATE"
    assert "wind-01" in d.target_assets
    assert d.derate_fraction == 0.0  # Wind exceeds cut-out speed -> 0.0
    assert d.valid_start_slot == 36
    assert d.valid_end_slot == 60
    assert 0.0 <= d.match_strength <= 1.0
    assert d.match_strength >= 0.6  # keyword + wind + window signals matched


def test_document_extractor_cmms_work_order() -> None:
    extractor = DocumentExtractorEngine()
    wo_text = """
    CMMS WORK ORDER #49201 - EMERGENCY SUBSTATION & BESS INSPECTION
    TARGET ASSET: BESS-B INVERTER BAY AND TIE-MAIN FEEDER.
    ACTION: SCHEDULED OUTAGE AND LINE BUSHING OVERHAUL.
    TIME WINDOW: FROM 10:00 TO 16:00.
    DERATE TIE-MAIN TRANSMISSION CAPACITY TO 40 MW.
    """
    directives = extractor.parse_document(wo_text, source_label="work_order_49201.pdf")
    assert len(directives) == 1
    d = directives[0]
    assert d.directive_type == "PLANNED_MAINTENANCE"
    assert "bess-b" in d.target_assets
    assert "tie-main" in d.target_assets
    assert d.tie_line_capacity_mw == 40.0
    assert d.requires_human_signoff is True
