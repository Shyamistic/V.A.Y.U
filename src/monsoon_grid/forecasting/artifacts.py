"""Part E -- versioned model/calibration artifacts (design §10).

Content digest is computed over the canonical bytes of the ARRAY CONTENTS
(name|dtype|shape|C-contiguous raw bytes), NOT the .npz file (which embeds
timestamps and is not reproducible). Floats are stored float64 unrounded; the
six-decimal rule is a JSON-boundary rule only (§9.1, §10.3).

``load_artifact(..., verify=True)`` recomputes the digest and REFUSES to return a
model on mismatch. No network import exists in this module (invariant: a test
asserts it). Layout: ``data/artifacts/<model_id>/<version>/{manifest.json,
arrays.npz,README.txt}`` under ``data/`` (not runtime/artifacts, to stay out of
the certificate glob).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from monsoon_grid.canonical import canonical_bytes

from .errors import (
    ArtifactConflict,
    ArtifactCorrupt,
    ArtifactDigestMismatch,
    ArtifactNotFound,
    ArtifactVersionUnsupported,
)

SCHEMA_MAJOR = 1
_SIZE_GUARD_BYTES = 25 * 1024 * 1024


def array_content_digest(arrays: dict[str, np.ndarray]) -> str:
    """SHA-256 over canonical array contents in sorted-name order (§10.3)."""
    h = hashlib.sha256()
    for name in sorted(arrays):
        arr = np.ascontiguousarray(arrays[name])
        header = canonical_bytes({"name": name, "dtype": arr.dtype.str, "shape": list(arr.shape)})
        h.update(header)
        h.update(arr.tobytes(order="C"))
    return h.hexdigest()


@dataclass(frozen=True)
class LoadedArtifact:
    model_id: str
    version: str
    manifest: dict
    arrays: dict[str, np.ndarray]


def save_artifact(
    root: Path,
    model_id: str,
    version: str,
    arrays: dict[str, np.ndarray],
    manifest: dict,
    *,
    allow_large: bool = False,
) -> str:
    """Write an immutable, digest-anchored artifact. Returns content_sha256."""
    content_sha = array_content_digest(arrays)
    d = Path(root) / model_id / version
    # Immutability: refuse to overwrite with a different digest.
    existing_manifest = d / "manifest.json"
    if existing_manifest.exists():
        old = json.loads(existing_manifest.read_text())
        if old.get("content_sha256") != content_sha:
            raise ArtifactConflict(f"{model_id}/{version} exists with a different digest")
    d.mkdir(parents=True, exist_ok=True)

    # Estimate size for the guard.
    total_bytes = sum(np.ascontiguousarray(a).nbytes for a in arrays.values())
    if total_bytes > _SIZE_GUARD_BYTES and not allow_large:
        raise ArtifactConflict(f"artifact {total_bytes} bytes exceeds 25 MB size guard (use allow_large)")

    full_manifest = dict(manifest)
    full_manifest["content_sha256"] = content_sha
    full_manifest["schema_major"] = SCHEMA_MAJOR

    # Atomic write: tmp then os.replace. Write through an open file handle so
    # numpy does not append its own ".npz" to the temp name.
    npz_tmp = d / "arrays.npz.tmp"
    with open(npz_tmp, "wb") as fh:
        np.savez(fh, **arrays)
    os.replace(npz_tmp, d / "arrays.npz")

    man_tmp = d / "manifest.json.tmp"
    man_tmp.write_text(json.dumps(full_manifest, sort_keys=True, indent=2))
    os.replace(man_tmp, d / "manifest.json")

    (d / "README.txt").write_text(
        full_manifest.get("regeneration_command", "see docs/forecasting.md for the regeneration command") + "\n"
    )
    return content_sha


def load_artifact(root: Path, model_id: str, version: str, *, verify: bool = True) -> LoadedArtifact:
    d = Path(root) / model_id / version
    manifest_path = d / "manifest.json"
    npz_path = d / "arrays.npz"
    if not manifest_path.exists():
        raise ArtifactNotFound(f"{model_id}/{version} manifest missing")
    if not npz_path.exists():
        raise ArtifactCorrupt(f"{model_id}/{version} arrays.npz missing")
    manifest = json.loads(manifest_path.read_text())

    major = manifest.get("schema_major")
    if major is None or int(major) > SCHEMA_MAJOR:
        raise ArtifactVersionUnsupported(f"unknown schema major {major}")

    try:
        with np.load(npz_path, allow_pickle=False) as data:
            arrays = {k: np.asarray(data[k]) for k in data.files}
    except Exception as exc:  # truncated / corrupt file
        raise ArtifactCorrupt(f"{model_id}/{version} arrays unreadable: {exc}") from exc

    if verify:
        recomputed = array_content_digest(arrays)
        if recomputed != manifest.get("content_sha256"):
            raise ArtifactDigestMismatch(
                f"{model_id}/{version} digest mismatch: {recomputed} != {manifest.get('content_sha256')}"
            )
    return LoadedArtifact(model_id=model_id, version=version, manifest=manifest, arrays=arrays)


__all__ = [
    "SCHEMA_MAJOR",
    "array_content_digest",
    "LoadedArtifact",
    "save_artifact",
    "load_artifact",
]
