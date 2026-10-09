"""Tests for the experiment harness: run_id stability, seeding, determinism,
no global RNG (design §8.11, invariant 8)."""

from __future__ import annotations

import pathlib
import sys

import numpy as np

# The ``experiments`` package lives at the repo root (the dir containing this
# ``tests/`` folder's parent). Put that root on sys.path so the module imports
# regardless of pytest's cwd/rootdir.
_REPO_ROOT = str(pathlib.Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import experiments._harness as H  # noqa: E402

_FORECASTING_DIR = pathlib.Path(H.__file__).resolve().parents[1] / "src" / "monsoon_grid" / "forecasting"


def test_run_id_stable_under_config_key_reorder():
    cfg_a = {"b": 2, "a": 1, "nested": {"y": 1, "x": 2}}
    cfg_b = {"a": 1, "b": 2, "nested": {"x": 2, "y": 1}}
    r1 = H.compute_run_id("exp", cfg_a, 7, {"d": "sha"})
    r2 = H.compute_run_id("exp", cfg_b, 7, {"d": "sha"})
    assert r1 == r2
    assert len(r1) == 16


def test_run_id_changes_with_seed():
    cfg = {"a": 1}
    assert H.compute_run_id("exp", cfg, 1, {}) != H.compute_run_id("exp", cfg, 2, {})


def test_named_generators_deterministic_and_distinct():
    g1 = H.make_generators(42, ["bootstrap", "scenarios", "pit_ties"])
    g2 = H.make_generators(42, ["bootstrap", "scenarios", "pit_ties"])
    a1 = g1["bootstrap"].random(5)
    a2 = g2["bootstrap"].random(5)
    np.testing.assert_array_equal(a1, a2)  # reproducible
    # Distinct streams per name.
    assert not np.array_equal(g1["scenarios"].random(5), g1["pit_ties"].random(5))


def test_no_global_rng_in_forecasting_package():
    offenders = []
    for path in _FORECASTING_DIR.rglob("*.py"):
        text = path.read_text()
        for bad in ("np.random.seed", "np.random.rand(", "np.random.randn", "np.random.default_rng"):
            if bad in text:
                offenders.append((path.name, bad))
    assert offenders == [], f"global/implicit RNG use found: {offenders}"


def test_determinism_digest_excludes_volatile():
    base = {"a": 1, "b": [1, 2, 3]}
    d1 = H.determinism_digest({**base, "created_at": "t1", "wall_clock_seconds": 1.0, "hostname": "x"})
    d2 = H.determinism_digest({**base, "created_at": "t2", "wall_clock_seconds": 99.0, "hostname": "y"})
    assert d1 == d2


def test_experiment_run_twice_identical_digest(tmp_path):
    def make():
        run = H.ExperimentRun(experiment="tiny", config={"k": 5}, seed=1, results_root=tmp_path)
        g = run.generators(["bootstrap"])
        run.record("sample", g["bootstrap"].random(3).tolist())
        return run.build_artifact()["determinism_digest"]

    assert make() == make()


def test_experiment_run_writes_artifact(tmp_path):
    run = H.ExperimentRun(
        experiment="tiny",
        config={"k": 5},
        seed=1,
        dataset_fingerprints={"synthetic": "sha0"},
        results_root=tmp_path,
    )
    run.record("metric", 0.5)
    out = run.write()
    assert out.exists()
    import json

    data = json.loads(out.read_text())
    assert data["experiment"] == "tiny"
    assert "determinism_digest" in data
    assert data["run_id"] == run.run_id


def test_budget_exhausted_flag():
    run = H.ExperimentRun(experiment="x", config={}, seed=1, total_budget_hours=0.0)
    # With a zero budget, elapsed >= 0 is immediately exhausted.
    assert run.budget_exhausted() is True


def test_requirements_file_installs_nothing_new():
    req = (pathlib.Path(H.__file__).resolve().parent / "requirements-experiments.txt").read_text()
    assert "install NOTHING" in req
    # pvlib/pandas appear only in the commented optional block.
    for line in req.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert not stripped.startswith("pvlib")
        assert not stripped.startswith("pandas")
