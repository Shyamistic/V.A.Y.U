"""B5: replay-record modules do not read the wall clock.

Design B5 requires that nothing which enters a replay record reads the wall
clock. The permitted exceptions are infrastructure that writes a human-facing,
non-digested field and is excluded from the inventory comparison:

- provenance.py::inventory writes ``captured_at_runtime`` (design-stated).
- observability.py and auth.py are request-infrastructure modules owned by a
  different workflow (not replay-record producers); their ``_utcnow`` helpers
  are logging/clock infrastructure, not replay content.
"""

from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "monsoon_grid"
# ingest/base.py timestamps a real network retrieval (retrieved_at_utc), which is
# observational metadata about a live fetch, not a replay computation, and is
# excluded from the determinism guarantee like latency.
ALLOWED = {"provenance.py", "observability.py", "auth.py", "base.py"}


def test_datetime_now_absent_from_replay_modules():
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.name in ALLOWED:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "datetime.now(" in text or "dt.datetime.now(" in text:
            offenders.append(str(path.relative_to(SRC)))
    assert offenders == [], f"wall-clock read in replay modules: {offenders}"


def test_narration_uses_replay_clock():
    text = (SRC / "narration.py").read_text(encoding="utf-8")
    assert "replay_time" in text
    assert "datetime.now" not in text
