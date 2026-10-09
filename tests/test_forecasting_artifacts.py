"""Tests for the digest-verified artifact store (design §10)."""

from __future__ import annotations

import inspect
import zipfile

import numpy as np
import pytest

from monsoon_grid.forecasting import artifacts as A
from monsoon_grid.forecasting.errors import (
    ArtifactConflict,
    ArtifactCorrupt,
    ArtifactDigestMismatch,
    ArtifactNotFound,
)


def _arrays():
    return {"coef": np.array([1.0, 2.0, 3.0]), "edges": np.array([[0.1, 0.2], [0.3, 0.4]])}


def test_round_trip_equality(tmp_path):
    arrays = _arrays()
    sha = A.save_artifact(tmp_path, "pv-residual-qr", "qr-v1-abc", arrays, {"regeneration_command": "run x"})
    loaded = A.load_artifact(tmp_path, "pv-residual-qr", "qr-v1-abc")
    assert loaded.manifest["content_sha256"] == sha
    np.testing.assert_array_equal(loaded.arrays["coef"], arrays["coef"])
    np.testing.assert_array_equal(loaded.arrays["edges"], arrays["edges"])


def test_digest_is_over_array_contents_not_file(tmp_path):
    # Same arrays saved to two versions -> identical content digest even though
    # the npz file bytes (with embedded timestamps) differ.
    arrays = _arrays()
    s1 = A.save_artifact(tmp_path, "m", "v1", arrays, {})
    s2 = A.save_artifact(tmp_path, "m", "v2", arrays, {})
    assert s1 == s2


def test_tamper_one_float_in_npz_raises(tmp_path):
    arrays = _arrays()
    A.save_artifact(tmp_path, "m", "v1", arrays, {})
    # Overwrite the npz with tampered contents (same filename inside).
    npz = tmp_path / "m" / "v1" / "arrays.npz"
    tampered = {"coef": np.array([1.0, 2.0, 99.0]), "edges": arrays["edges"]}
    np.savez(npz, **tampered)
    with pytest.raises(ArtifactDigestMismatch):
        A.load_artifact(tmp_path, "m", "v1")


def test_tamper_manifest_digest_raises(tmp_path):
    arrays = _arrays()
    A.save_artifact(tmp_path, "m", "v1", arrays, {})
    man = tmp_path / "m" / "v1" / "manifest.json"
    import json

    data = json.loads(man.read_text())
    data["content_sha256"] = "0" * 64
    man.write_text(json.dumps(data))
    with pytest.raises(ArtifactDigestMismatch):
        A.load_artifact(tmp_path, "m", "v1")


def test_truncated_file_raises_corrupt(tmp_path):
    arrays = _arrays()
    A.save_artifact(tmp_path, "m", "v1", arrays, {})
    npz = tmp_path / "m" / "v1" / "arrays.npz"
    raw = npz.read_bytes()
    npz.write_bytes(raw[: len(raw) // 2])  # truncate
    with pytest.raises((ArtifactCorrupt, zipfile.BadZipFile)):
        A.load_artifact(tmp_path, "m", "v1")


def test_missing_artifact_raises_not_found(tmp_path):
    with pytest.raises(ArtifactNotFound):
        A.load_artifact(tmp_path, "nope", "v1")


def test_conflict_on_different_digest_same_version(tmp_path):
    A.save_artifact(tmp_path, "m", "v1", _arrays(), {})
    with pytest.raises(ArtifactConflict):
        A.save_artifact(tmp_path, "m", "v1", {"coef": np.array([9.0])}, {})


def test_loads_under_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("MONSOON_GRID_FORECASTING_OFFLINE", "1")
    A.save_artifact(tmp_path, "m", "v1", _arrays(), {})
    loaded = A.load_artifact(tmp_path, "m", "v1")
    assert "coef" in loaded.arrays


def test_module_has_no_network_import():
    src = inspect.getsource(A)
    assert "import httpx" not in src
    assert "httpx" not in src


def test_determinism_digest_equal_on_twice_run(tmp_path):
    s1 = A.array_content_digest(_arrays())
    s2 = A.array_content_digest(_arrays())
    assert s1 == s2
