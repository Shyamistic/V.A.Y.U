#!/usr/bin/env python3
"""Source revision resolution without git (design §9.6, AC-40).

Resolves an image revision identifier in four steps and always succeeds, and
always computes a content `source_tree_digest` the certificate-binding workflow
can consume. The workspace is not a git repository today, so the digest fallback
is the operative branch.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

# Exactly the runtime inputs baked into the image. Adding a runtime input to the
# image REQUIRES adding it here, or the digest stops identifying deployed source.
_DIGEST_GLOBS: tuple[tuple[str, str], ...] = (
    ("src/monsoon_grid", "**/*.py"),
    ("src/monsoon_grid/ui", "**/*"),
    ("fixtures", "**/*"),
    ("experiments/results", "**/*.json"),
    ("trust", "**/*"),
    ("deploy", "requirements-linux.txt"),
    ("deploy", "Dockerfile"),
    ("deploy", "asgi.py"),
    ("deploy", "cadence.py"),
    ("deploy", "source_revision.py"),
    ("deploy", "entrypoint.sh"),
    (".", "pyproject.toml"),
)


def _iter_files(root: Path) -> list[Path]:
    seen: set[Path] = set()
    for base, pattern in _DIGEST_GLOBS:
        base_path = (root / base).resolve()
        if not base_path.exists():
            continue
        if any(ch in pattern for ch in "*?"):
            for p in sorted(base_path.glob(pattern)):
                if p.is_file() and "__pycache__" not in p.parts and "node_modules" not in p.parts and p.suffix != ".pyc":
                    seen.add(p)
        else:
            p = base_path / pattern if base_path.is_dir() else base_path
            if p.is_file():
                seen.add(p)
    return sorted(seen)


def source_tree_digest(root: Path) -> str:
    root = root.resolve()
    hasher = hashlib.sha256()
    for path in _iter_files(root):
        rel = path.relative_to(root).as_posix()
        file_hash = hashlib.sha256(path.read_bytes()).digest()
        hasher.update(rel.encode("utf-8") + b"\0" + file_hash + b"\n")
    return hasher.hexdigest()


def resolve_source_revision(root: Path) -> tuple[str, str]:
    """Return (revision, source_tree_digest)."""
    root = root.resolve()
    digest = source_tree_digest(root)

    override = os.environ.get("SOURCE_REVISION")
    if override:
        return override, digest

    commit = os.environ.get("COMMIT_SHA")
    if commit:
        return f"sha-{commit[:12]}", digest

    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(root), capture_output=True, text=True, timeout=5
        )
        if sha.returncode == 0 and sha.stdout.strip():
            rev = f"git-{sha.stdout.strip()[:12]}"
            dirty = subprocess.run(
                ["git", "status", "--porcelain"], cwd=str(root), capture_output=True, text=True, timeout=5
            )
            if dirty.returncode == 0 and dirty.stdout.strip():
                rev += "-dirty"
            return rev, digest
    except (OSError, subprocess.SubprocessError):
        pass

    return f"tree-{digest[:12]}", digest


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


if __name__ == "__main__":
    revision, digest = resolve_source_revision(_repo_root())
    print(f"SOURCE_REVISION={revision}")
    print(f"SOURCE_TREE_DIGEST={digest}")
    sys.exit(0)
