"""C3: frontend/ is removed and no reference to it remains."""

from __future__ import annotations

from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[1]


def test_frontend_directory_absent():
    assert (PRODUCT_ROOT / "frontend").exists() is False


def test_no_frontend_reference_in_source_scripts_or_config():
    search_roots = [PRODUCT_ROOT / "src", PRODUCT_ROOT / "scripts", PRODUCT_ROOT / ".github", PRODUCT_ROOT / "pyproject.toml"]
    offenders = []
    for root in search_roots:
        if root.is_file():
            files = [root]
        else:
            files = [p for p in root.rglob("*") if p.is_file() and p.suffix in {".py", ".ps1", ".yml", ".yaml", ".toml"}]
        for path in files:
            if "frontend" in path.read_text(encoding="utf-8", errors="ignore"):
                offenders.append(str(path))
    assert offenders == [], offenders
