from pathlib import Path


def test_static_ui_has_permanent_scope_and_gating_labels():
    root = Path(__file__).parents[1] / "src" / "monsoon_grid" / "ui"
    html = (root / "index.html").read_text(encoding="utf-8")
    js = (root / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "SIMULATION" in html and "REPLAY" in html and "NO LIVE CONTROL / NO MARKET SUBMISSION" in html
    assert "interactiveDecision?.status === 'ACCEPTED'" in js
    assert "disabled={!canApprove}" in js
    assert "'/approvals'" in js and "'/validate'" in js
    assert "localStorage" not in js and "sessionStorage" not in js
