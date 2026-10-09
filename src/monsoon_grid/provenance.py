"""Protected-input inventory with explicit missing and symlink handling."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .canonical import canonical_bytes
from .config import PRODUCT_ROOT, PROVENANCE_ROOT

PROTECTED_MANIFEST = PRODUCT_ROOT / "protected-inputs.manifest.json"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _entry(path: Path, root: Path | None = None) -> dict[str, Any]:
    root = root or path.parent
    item: dict[str, Any] = {
        "path": str(path),
        "kind": "missing",
        "exists": False,
        "size": None,
        "sha256": None,
        "symlink": False,
        "symlink_target": None,
        "symlink_followed": False,
        "error": None,
    }
    try:
        info = path.lstat()
    except OSError as exc:
        item["error"] = f"{type(exc).__name__}: {exc}"
        return item
    item["exists"] = True
    if path.is_symlink():
        item["kind"] = "symlink"
        item["symlink"] = True
        item["symlink_target"] = os.readlink(path)
        return item
    if path.is_file():
        item["kind"] = "file"
        item["size"] = info.st_size
        try:
            item["sha256"] = _sha256_file(path)
        except OSError as exc:
            item["error"] = f"{type(exc).__name__}: {exc}"
        return item
    if path.is_dir():
        item["kind"] = "directory"
        children: list[dict[str, Any]] = []
        try:
            for child in sorted(path.rglob("*"), key=lambda value: str(value).lower()):
                if child.is_symlink():
                    children.append(_entry(child, path))
                elif child.is_file():
                    children.append(_entry(child, path))
        except OSError as exc:
            item["error"] = f"{type(exc).__name__}: {exc}"
        item["files"] = children
        item["sha256"] = hashlib.sha256(canonical_bytes(children)).hexdigest()
        item["size"] = sum(child.get("size") or 0 for child in children)
        return item
    item["kind"] = "other"
    item["size"] = info.st_size
    return item


def _vayu_commit(vayu_path: Path) -> str | None:
    if not (vayu_path / ".git").exists():
        return None
    try:
        result = subprocess.run(["git", "-C", str(vayu_path), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10, check=False)
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def protected_paths() -> list[Path]:
    try:
        data = json.loads(PROTECTED_MANIFEST.read_text(encoding="utf-8"))
        return [Path(value) for value in data.get("paths", [])]
    except (OSError, json.JSONDecodeError):
        return []


def inventory(output: Path, *, compare_to: Path | None = None) -> dict[str, Any]:
    paths = protected_paths()
    entries = [_entry(path) for path in paths]
    vayu = next((path for path in paths if path.name == "vayu" and path.is_dir()), None)
    result: dict[str, Any] = {
        "schema_version": "1.0",
        "kind": "protected-input-inventory",
        "captured_at_runtime": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "protected_paths": entries,
        "vayu_git_commit": _vayu_commit(vayu) if vayu else None,
        "product_owned_excluded": [str(PRODUCT_ROOT / "runtime")],
        "comparison": None,
    }
    if compare_to:
        try:
            baseline = json.loads(compare_to.read_text(encoding="utf-8"))
            left = {"protected_paths": baseline.get("protected_paths", []), "vayu_git_commit": baseline.get("vayu_git_commit")}
            right = {"protected_paths": entries, "vayu_git_commit": result["vayu_git_commit"]}
            result["comparison"] = {
                "match": canonical_bytes(left) == canonical_bytes(right),
                "baseline": str(compare_to),
                "differences": [] if canonical_bytes(left) == canonical_bytes(right) else ["protected input metadata differs"],
            }
            if not result["comparison"]["match"]:
                raise RuntimeError("protected-input comparison failed")
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"cannot compare protected inventory: {exc}") from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
