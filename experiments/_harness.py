"""Experiment harness: run id, seeding, budget, artifact writer (design §8.11).

- ``run_id = sha256(canonical({experiment, config, seed, dataset_ids+digests,
  code_version}))[:16]``.
- A single integer seed -> ``numpy.random.SeedSequence`` -> named child
  ``Generator(PCG64)`` instances. NO global ``np.random`` anywhere (invariant 8).
- ``total_budget_hours`` enforced between work units; on exhaustion the run
  records ``budget_truncated: true`` + the un-run cell list, never a null result.
- ``determinism_digest`` is computed over the artifact EXCLUDING wall-clock/host.
"""

from __future__ import annotations

import json
import platform
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from monsoon_grid.canonical import canonical_bytes, sha256_hex
from monsoon_grid.forecasting.codeid import code_version

_VOLATILE_KEYS = {"created_at", "wall_clock_seconds", "hostname", "run_duration_seconds"}


def make_generators(seed: int, names: list[str]) -> dict[str, np.random.Generator]:
    """Spawn named child PCG64 generators from one integer seed (invariant 8)."""
    ss = np.random.SeedSequence(seed)
    children = ss.spawn(len(names))
    return {name: np.random.Generator(np.random.PCG64(child)) for name, child in zip(names, children)}


def compute_run_id(experiment: str, config: dict, seed: int, dataset_fingerprints: dict) -> str:
    cv = code_version()
    payload = {
        "experiment": experiment,
        "config": config,
        "seed": seed,
        "datasets": dataset_fingerprints,
        "code_version": cv,
    }
    return sha256_hex(payload)[:16]


def environment_block() -> dict:
    return {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "numpy_version": np.__version__,
    }


def determinism_digest(artifact: dict) -> str:
    """Digest over the artifact excluding volatile wall-clock/host fields."""
    clean = {k: v for k, v in artifact.items() if k not in _VOLATILE_KEYS}
    return sha256_hex(clean)


@dataclass
class ExperimentRun:
    experiment: str
    config: dict
    seed: int
    dataset_fingerprints: dict = field(default_factory=dict)
    total_budget_hours: float = 2.0
    results_root: Path | None = None
    _start: float = field(default_factory=time.monotonic)
    budget_truncated: bool = False
    unrun_cells: list[str] = field(default_factory=list)
    _artifact_body: dict = field(default_factory=dict)

    def generators(self, names: list[str]) -> dict[str, np.random.Generator]:
        return make_generators(self.seed, names)

    @property
    def run_id(self) -> str:
        return compute_run_id(self.experiment, self.config, self.seed, self.dataset_fingerprints)

    def budget_exhausted(self) -> bool:
        elapsed_hours = (time.monotonic() - self._start) / 3600.0
        return elapsed_hours >= self.total_budget_hours

    def record(self, key: str, value) -> None:
        self._artifact_body[key] = value

    def build_artifact(self) -> dict:
        artifact = {
            "experiment": self.experiment,
            "run_id": self.run_id,
            "code_version": code_version(),
            "config": self.config,
            "seed": self.seed,
            "datasets": self.dataset_fingerprints,
            "environment": environment_block(),
            "budget_truncated": self.budget_truncated,
            "unrun_cells": self.unrun_cells,
            **self._artifact_body,
        }
        artifact["determinism_digest"] = determinism_digest(artifact)
        return artifact

    def write(self) -> Path:
        if self.results_root is None:
            raise ValueError("results_root must be set to write an artifact")
        artifact = self.build_artifact()
        artifact["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        artifact["wall_clock_seconds"] = round(time.monotonic() - self._start, 3)
        out_dir = Path(self.results_root) / self.experiment
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{self.run_id}.json"
        tmp = out_path.with_suffix(".json.tmp")
        tmp.write_bytes(canonical_bytes(artifact))
        tmp.replace(out_path)
        return out_path


def load_config(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


__all__ = [
    "make_generators",
    "compute_run_id",
    "environment_block",
    "determinism_digest",
    "ExperimentRun",
    "load_config",
]
