"""C4: protected-input manifest shape (CI-safe) and machine-local comparison."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from monsoon_grid.provenance import inventory, protected_paths

PRODUCT_ROOT = Path(__file__).resolve().parents[1]


def test_protected_manifest_shape():
    """Machine-independent: the manifest file itself is well formed. Runs on CI."""
    raw = json.loads((PRODUCT_ROOT / "protected-inputs.manifest.json").read_text(encoding="utf-8"))
    assert raw["kind"] == "protected-input-manifest"
    paths = raw["paths"]
    assert isinstance(paths, list)
    assert len(paths) == 15
    assert len(protected_paths()) == len(paths)
    assert all(isinstance(p, str) and p.strip() for p in paths)
    assert len(set(paths)) == len(paths)
    assert all(Path(p).is_absolute() for p in paths)
    assert all(Path(p).suffix in {"", ".md", ".zip", ".pdf", ".egg-info"} for p in paths)
    assert all(Path(p).name for p in paths)


@pytest.mark.machine_local
def test_protected_inventory_comparison_is_stable(tmp_path):
    """Authoring-workstation only: the real inputs exist and are unchanged."""
    missing = [p for p in protected_paths() if not p.exists()]
    if missing:
        pytest.skip(f"protected inputs absent on this machine: {missing}")
    first_path = tmp_path / "baseline.json"
    second_path = tmp_path / "after.json"
    inventory(first_path)
    inventory(second_path, compare_to=first_path)
    result = json.loads(second_path.read_text())
    assert result["comparison"]["match"]
    assert len(result["protected_paths"]) == 15
    assert sum(1 for e in result["protected_paths"] if e["kind"] == "missing") == 0
