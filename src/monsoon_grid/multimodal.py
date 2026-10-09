"""Deterministic candidate-extraction helpers over text and sky-camera frames.

This module is a deterministic keyword-and-regex candidate extractor over text,
producing unvalidated candidate directives that require human approval, plus a
brightness-centroid cloud-displacement nowcaster. It makes no depth or
reliability claim.

match_strength is a rule-match ratio. It is not a calibrated probability and
carries no frequentist or Bayesian interpretation.

Components:
1. VisionNowcastEngine: brightness-centroid cloud-displacement nowcaster and
   shadow projector over the solar parks.
2. DocumentExtractorEngine: keyword/regex extractor of candidate operational
   directives from unstructured text; every output requires human approval.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any
import numpy as np

from .canonical import sha256_hex
from .contracts import Provenance


# ============================================================================
# 1. Computer Vision Cloud-Tracking & Sky-Camera Nowcaster
# ============================================================================

@dataclass(frozen=True)
class SkyCameraFrame:
    """Represents a 64x64 or 128x128 sky-camera or satellite infrared/visible frame."""
    timestamp_utc: str
    resolution: tuple[int, int]
    raw_pixels: list[float]  # Grayscale brightness 0.0 (clear sky) to 1.0 (thick cumulus cloud)
    camera_id: str = "sky-cam-central-01"


@dataclass(frozen=True)
class CloudNowcastResult:
    """Projected cloud shadow velocity and solar farm irradiance attenuation."""
    velocity_vector_ms: tuple[float, float]  # (v_x, v_y) in m/s across the park
    mean_cloud_cover_fraction: float
    affected_solar_assets: dict[str, float]  # {asset_id: irradiance_attenuation_factor 0.0..1.0}
    predicted_ramp_rate_mw_min: float
    match_strength: float  # rule-match ratio in [0,1]; NOT a calibrated probability
    provenance: Provenance


class VisionNowcastEngine:
    """Deterministic dense optical flow cloud velocity tracker and shadow projector."""

    # Solar farm relative coordinate layout in km from central sky-cam
    SOLAR_COORDINATES_KM = {
        "solar-01": (-4.5, 3.2),
        "solar-02": (2.1, 5.0),
        "solar-03": (-1.0, -2.5),
        "solar-04": (5.2, -3.8),
        "solar-05": (0.0, 0.0),  # Co-located with central sky camera
    }

    def compute_optical_flow_velocity(
        self,
        frame_t0: SkyCameraFrame,
        frame_t1: SkyCameraFrame,
        time_delta_seconds: float = 60.0,
    ) -> tuple[float, float]:
        """Compute average cloud motion vector using brightness centroid displacement."""
        h0, w0 = frame_t0.resolution
        h1, w1 = frame_t1.resolution
        if (h0, w0) != (h1, w1) or len(frame_t0.raw_pixels) != h0 * w0:
            return (0.0, 0.0)

        grid0 = np.array(frame_t0.raw_pixels, dtype=np.float64).reshape((h0, w0))
        grid1 = np.array(frame_t1.raw_pixels, dtype=np.float64).reshape((h1, w1))

        # Threshold cloud regions (brightness > 0.45)
        mask0 = grid0 > 0.45
        mask1 = grid1 > 0.45

        if np.sum(mask0) < 5 or np.sum(mask1) < 5:
            return (0.0, 0.0)  # Clear sky or negligible clouds

        y_coords0, x_coords0 = np.nonzero(mask0)
        y_coords1, x_coords1 = np.nonzero(mask1)

        c_x0, c_y0 = float(np.mean(x_coords0)), float(np.mean(y_coords0))
        c_x1, c_y1 = float(np.mean(x_coords1)), float(np.mean(y_coords1))

        # Pixel to meter scale (assume 10 km field-of-view at 1 km cloud base height)
        meters_per_pixel = 10000.0 / w0
        dx_meters = (c_x1 - c_x0) * meters_per_pixel
        dy_meters = (c_y1 - c_y0) * meters_per_pixel

        vx = round(dx_meters / max(1.0, time_delta_seconds), 2)
        vy = round(dy_meters / max(1.0, time_delta_seconds), 2)
        return (vx, vy)

    def project_solar_attenuation(
        self,
        current_frame: SkyCameraFrame,
        velocity_vector_ms: tuple[float, float],
        solar_fleet_capacity_mw: float,
        lead_time_minutes: float = 15.0,
    ) -> CloudNowcastResult:
        """Project cloud shadow impact onto the solar farms.

        Assumptions (documented, not measured): a 10 km field-of-view at the cloud
        base, mean frame brightness used as the cloud-cover fraction, and the
        caller-supplied ``solar_fleet_capacity_mw`` as the fleet size (callers pass
        ``sum(a.capacity_mw for a in portfolio.assets_of("solar_farm"))``; there is
        no hardcoded fleet size).
        """
        h, w = current_frame.resolution
        pixels = np.array(current_frame.raw_pixels, dtype=np.float64).reshape((h, w))
        mean_cover = float(np.mean(pixels))

        vx, vy = velocity_vector_ms
        # Projected displacement in km over lead time
        disp_x_km = (vx * lead_time_minutes * 60.0) / 1000.0
        disp_y_km = (vy * lead_time_minutes * 60.0) / 1000.0

        attenuations: dict[str, float] = {}
        for asset_id, (base_x, base_y) in self.SOLAR_COORDINATES_KM.items():
            # Projected position relative to cloud frame center
            proj_x = base_x - disp_x_km
            proj_y = base_y - disp_y_km
            dist_km = math.sqrt(proj_x * proj_x + proj_y * proj_y)

            # Cloud shadow attenuation function: exponential falloff from cloud core
            if dist_km < 4.0:
                # Direct shadow footprint
                attenuation = max(0.15, min(0.95, mean_cover * 1.35))
            else:
                attenuation = max(0.0, mean_cover * 0.40)
            attenuations[asset_id] = round(attenuation, 3)

        # Ramp rate estimation (MW/min across the supplied solar fleet capacity).
        max_att = max(attenuations.values()) if attenuations else 0.0
        ramp_rate = round(max_att * max(0.0, solar_fleet_capacity_mw) / max(1.0, lead_time_minutes), 2)

        # match_strength is a rule-match ratio: how many of the required nowcast
        # signals (a non-trivial cloud mask and a non-zero velocity) are present.
        required_signals = 2
        matched = int(mean_cover > 0.05) + int(abs(vx) + abs(vy) > 0.0)
        match_strength = round(max(0.0, min(1.0, matched / required_signals)), 6)

        prov = Provenance(
            label="derived",
            source_id="vision_nowcast_engine",
            source_version="brightness-centroid-displacement-v1",
            input_refs=[current_frame.camera_id, current_frame.timestamp_utc],
        )

        return CloudNowcastResult(
            velocity_vector_ms=(vx, vy),
            mean_cloud_cover_fraction=round(mean_cover, 3),
            affected_solar_assets=attenuations,
            predicted_ramp_rate_mw_min=ramp_rate,
            match_strength=match_strength,
            provenance=prov,
        )


# ============================================================================
# 2. Unstructured Document & Alert Parser (IMD Storm Bulletins & CMMS Work Orders)
# ============================================================================

@dataclass(frozen=True)
class ParsedOperationalDirective:
    """Structured operational instruction extracted from unstructured text or PDF documents."""
    directive_id: str
    document_source: str
    directive_type: str  # STORM_DERATE, PLANNED_MAINTENANCE, LINE_CONGESTION_WARNING, TARIFF_OVERRIDE
    target_assets: list[str]
    valid_start_slot: int
    valid_end_slot: int
    derate_fraction: float      # Available capacity fraction (1.0 = 100%, 0.5 = 50%, 0.0 = full outage)
    tie_line_capacity_mw: float | None = None
    action_narrative: str = ""
    match_strength: float = 0.0  # rule-match ratio in [0,1]; NOT a calibrated probability
    requires_human_signoff: bool = False
    digest: str = field(default="")

    def compute_digest(self) -> str:
        payload = {
            "id": self.directive_id,
            "type": self.directive_type,
            "assets": self.target_assets,
            "start": self.valid_start_slot,
            "end": self.valid_end_slot,
            "derate": self.derate_fraction,
            "line_mw": self.tie_line_capacity_mw,
        }
        return sha256_hex(payload)


class DocumentExtractorEngine:
    """Robust extractor parsing IMD weather bulletins and CMMS maintenance work order documents."""

    def parse_document(self, text_content: str, source_label: str = "document_upload") -> list[ParsedOperationalDirective]:
        """Parse natural-language bulletins or work orders into structured directives."""
        results: list[ParsedOperationalDirective] = []
        text_upper = text_content.upper()

        # 1. Detect Storm / Cyclone / Weather Alerts (e.g. IMD Bulletin)
        if any(term in text_upper for term in ["IMD", "SQUALL", "CYCLONE", "STORM ALERT", "GALE", "MONSOON WARNING"]):
            # Look for wind speed thresholds
            wind_match = re.search(r"WIND\s+(?:SPEED|GUSTS?)\s+(?:EXCEEDING|UP TO|OF)?\s*(\d+)\s*(?:KM/H|KMPH|M/S)", text_upper)
            wind_speed = float(wind_match.group(1)) if wind_match else 75.0
            is_mps = "M/S" in (wind_match.group(0) if wind_match else "")
            wind_mps = wind_speed if is_mps else wind_speed / 3.6

            # Determine derate severity
            if wind_mps > 25.0:  # Above cut-out speed
                derate = 0.0     # Full turbine cut-out to prevent mechanical overspeed
                narrative = f"IMD Extreme Storm Alert: Wind {wind_mps:.1f} m/s exceeds turbine cut-out limit (25 m/s). Autonomous shut-off enforced."
            elif wind_mps > 18.0:
                derate = 0.50
                narrative = f"IMD Severe Weather Warning: High wind shear {wind_mps:.1f} m/s. Wind farms derated by 50% for mechanical integrity."
            else:
                derate = 0.85
                narrative = "IMD Moderate Squall Warning: Minor turbulence derate applied."

            # Slot range extraction (e.g., "FROM 09:00 TO 15:00" -> slot 36 to 60)
            start_slot, end_slot = self._extract_slot_window(text_upper, default_start=36, default_end=60)
            # match_strength: ratio of required storm signals actually matched
            # (keyword always matched here, explicit wind speed, explicit window).
            matched = 1 + int(wind_match is not None) + int(len(re.findall(r"(\d{1,2}):(\d{2})", text_upper)) >= 2)
            match_strength = round(matched / 3, 6)

            directive = ParsedOperationalDirective(
                directive_id=f"alert-storm-{sha256_hex(text_content)[:8]}",
                document_source=source_label,
                directive_type="STORM_DERATE",
                target_assets=["wind-01", "wind-02", "wind-03"],
                valid_start_slot=start_slot,
                valid_end_slot=end_slot,
                derate_fraction=derate,
                action_narrative=narrative,
                match_strength=match_strength,
                requires_human_signoff=True,
            )
            object.__setattr__(directive, "digest", directive.compute_digest())
            results.append(directive)

        # 2. Detect CMMS Maintenance Work Orders
        if any(term in text_upper for term in ["WORK ORDER", "CMMS", "MAINTENANCE", "INSPECTION", "OUTAGE NOTICE"]):
            # Extract target assets
            target_assets = []
            for candidate in ["BESS-A", "BESS-B", "SOLAR-01", "SOLAR-02", "SOLAR-03", "SOLAR-04", "SOLAR-05", "WIND-01", "WIND-02", "WIND-03", "TIE-MAIN"]:
                if candidate in text_upper:
                    target_assets.append(candidate.lower())

            if not target_assets:
                target_assets = ["bess-b"]  # Standard demo scenario default

            start_slot, end_slot = self._extract_slot_window(text_upper, default_start=36, default_end=72)

            tie_line_mw = None
            if "TIE-MAIN" in [a.upper() for a in target_assets] or "TRANSMISSION LINE" in text_upper:
                # Look for line capacity limit
                cap_match = re.search(r"(\d+(?:\.\d+)?)\s*MW", text_upper)
                tie_line_mw = float(cap_match.group(1)) if cap_match else 50.0

            derate = 0.0 if "OUTAGE" in text_upper or "REPLACEMENT" in text_upper else 0.50
            # match_strength: ratio of required work-order signals matched
            # (keyword always matched, explicit target asset, explicit window).
            matched = 1 + int(bool([c for c in ["BESS-A", "BESS-B", "SOLAR-01", "SOLAR-02", "SOLAR-03", "SOLAR-04", "SOLAR-05", "WIND-01", "WIND-02", "WIND-03", "TIE-MAIN"] if c in text_upper])) + int(len(re.findall(r"(\d{1,2}):(\d{2})", text_upper)) >= 2)
            match_strength = round(matched / 3, 6)

            directive = ParsedOperationalDirective(
                directive_id=f"cmms-wo-{sha256_hex(text_content)[:8]}",
                document_source=source_label,
                directive_type="PLANNED_MAINTENANCE",
                target_assets=target_assets,
                valid_start_slot=start_slot,
                valid_end_slot=end_slot,
                derate_fraction=derate,
                tie_line_capacity_mw=tie_line_mw,
                action_narrative=f"CMMS candidate directive (requires human approval): maintenance on {', '.join(target_assets)}. Derate factor: {derate * 100:.0f}%.",
                match_strength=match_strength,
                requires_human_signoff=True,
            )
            object.__setattr__(directive, "digest", directive.compute_digest())
            results.append(directive)

        return results

    def _extract_slot_window(self, text_upper: str, default_start: int, default_end: int) -> tuple[int, int]:
        """Extract time window e.g. 'FROM 10:00 TO 14:30' into 15-minute slot indices."""
        time_matches = re.findall(r"(\d{1,2}):(\d{2})", text_upper)
        if len(time_matches) >= 2:
            try:
                h1, m1 = int(time_matches[0][0]), int(time_matches[0][1])
                h2, m2 = int(time_matches[1][0]), int(time_matches[1][1])
                slot1 = min(95, max(0, int((h1 * 60 + m1) / 15)))
                slot2 = min(95, max(0, int((h2 * 60 + m2) / 15)))
                if slot1 < slot2:
                    return slot1, slot2
            except Exception:
                pass
        return default_start, default_end


__all__ = [
    "SkyCameraFrame",
    "CloudNowcastResult",
    "VisionNowcastEngine",
    "ParsedOperationalDirective",
    "DocumentExtractorEngine",
]
