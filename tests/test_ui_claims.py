"""B1/B7: the UI carries no false capability claims and no service worker."""

from __future__ import annotations

from pathlib import Path

import pytest

UI_ROOT = Path(__file__).resolve().parents[1] / "src" / "monsoon_grid" / "ui"

# The five G9-mandated strings plus two extra claims removed by B1.
BANNED = [
    "12 ms",
    "OPTIMAL_FEASIBLE",
    "Zero Constraint",
    "Ed25519 Verified",
    "Deterministic HiGHS",
    "[React 18]",
    "56475aa75463474c0285df5dbf2bcab73da651358839e9b77481b2eab107708c",
]


def _ui_files():
    return [p for p in UI_ROOT.rglob("*") if p.is_file() and "node_modules" not in p.parts and p.suffix in {".html", ".js", ".ts", ".tsx", ".css", ".json"}]


@pytest.mark.parametrize("banned", BANNED)
def test_banned_string_absent(banned):
    for path in _ui_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert banned not in text, f"{banned!r} found in {path.name}"


def test_service_worker_absent():
    assert not (UI_ROOT / "service-worker.js").exists()


def test_no_ui_offline_capability_claim():
    # The UI must not claim to work offline (the backend replay offline property
    # is a separate, genuine claim kept in README).
    for path in _ui_files():
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        assert "offline-capable" not in text
        assert "works offline" not in text
