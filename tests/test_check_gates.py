"""C5: the gate harness passes with G1/G2/G6/G9/G10 blocking and G3 warning.

This test invokes the gate script as a subprocess against a tmp runtime root and
asserts the documented PASS/WARN split and exit 0.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PRODUCT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PRODUCT_ROOT / "scripts" / "check_gates.py"
VENV_PYTHON = PRODUCT_ROOT.parent / ".venv" / "Scripts" / "python.exe"


@pytest.mark.skipif(not VENV_PYTHON.exists(), reason="pinned venv executor not present")
def test_gates_pass_with_blocking_warning_split(tmp_path):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(PRODUCT_ROOT / "src"), str(PRODUCT_ROOT / "runtime" / "site-packages")])
    result = subprocess.run(
        [str(VENV_PYTHON), str(SCRIPT), "--runtime-root", str(tmp_path / "runtime-test")],
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
    )
    out = result.stdout
    assert result.returncode == 0, f"gates failed: {out}\n{result.stderr}"
    for gate in ("G1", "G2", "G6", "G9", "G10"):
        assert f"{gate} PASS BLOCKING" in out, out
    assert "G3 " in out and "WARNING" in out
    # gate-report.json must exist and name the owning workflow.
    report = tmp_path / "runtime-test" / "artifacts" / "gate-report.json"
    assert report.exists()
    assert "phase2-multiperiod-optimizer" in report.read_text(encoding="utf-8")
