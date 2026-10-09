"""Code identity digest (design §8.11, MEDIUM 20).

``git rev-parse`` reports *not a git repository* in this workspace, so there is
no commit SHA. ``code_version`` is therefore a content digest over the owned
source tree::

    code_version = sha256(canonical([ (relpath, sha256(file_bytes)) sorted by path
                          for forecasting/**/*.py + experiments/**/*.py
                            + experiments/configs/*.json ]))

The first 16 hex go into ``run_id``; the full digest is stored in every artifact.
This digest *is* the code identity in the absence of VCS metadata.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from monsoon_grid.canonical import sha256_hex

# Resolve the repo roots relative to this file: .../src/monsoon_grid/forecasting/codeid.py
_FORECASTING_DIR = Path(__file__).resolve().parent
_SRC_ROOT = _FORECASTING_DIR.parents[1]  # .../src
_REPO_ROOT = _SRC_ROOT.parent  # .../monsoon_grid
_EXPERIMENTS_DIR = _REPO_ROOT / "experiments"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _collect(roots_and_globs: list[tuple[Path, str]]) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for root, pattern in roots_and_globs:
        if not root.exists():
            continue
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            # Relative to the repo root so the digest is location-stable.
            rel = path.resolve().relative_to(_REPO_ROOT).as_posix()
            entries.append((rel, _sha256_file(path)))
    entries.sort(key=lambda e: e[0])
    return entries


def code_version(
    *,
    forecasting_dir: Path | None = None,
    experiments_dir: Path | None = None,
) -> str:
    """Compute the full ``code_version`` digest over the owned source tree."""
    fdir = forecasting_dir or _FORECASTING_DIR
    edir = experiments_dir or _EXPERIMENTS_DIR
    entries = _collect(
        [
            (fdir, "**/*.py"),
            (edir, "**/*.py"),
            (edir / "configs", "*.json"),
        ]
    )
    return sha256_hex(entries)


def code_version_short(**kwargs) -> str:
    return code_version(**kwargs)[:16]


def digest_tree(root: Path, pattern: str) -> str:
    """Digest an arbitrary tree (used by tests to simulate edits)."""
    entries: list[tuple[str, str]] = []
    for path in sorted(root.glob(pattern)):
        if path.is_file():
            entries.append((path.relative_to(root).as_posix(), _sha256_file(path)))
    entries.sort(key=lambda e: e[0])
    return sha256_hex(entries)


__all__ = ["code_version", "code_version_short", "digest_tree"]
