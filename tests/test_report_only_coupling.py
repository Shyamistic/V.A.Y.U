"""D1/D2: DSM and obligation values are report-only; the optimizer never sees them."""

from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "monsoon_grid"


def test_planner_does_not_reference_dsm_or_obligation():
    text = (SRC / "planner.py").read_text(encoding="utf-8")
    assert "dsm" not in text
    assert "obligation" not in text


def test_fallback_does_not_reference_dsm_or_obligation():
    text = (SRC / "fallback.py").read_text(encoding="utf-8")
    assert "dsm" not in text
    assert "obligation" not in text
