#!/usr/bin/env python3
"""Manifest drift gate (design §9.1, AC-31).

Parses pyproject.toml `[project].dependencies`, the Windows `requirements.lock`,
and the two Linux manifests, then asserts:

  1. every DIRECT dependency pin (the ones named in pyproject) agrees across
     pyproject, requirements.lock, and requirements-linux.txt; and
  2. the union of the two Linux manifests covers every transitive distribution
     present in requirements.lock, except the documented Windows-only / dev-only
     exclusion list.

Exits non-zero and prints a diff on any disagreement. Pure stdlib so it runs in
the first Cloud Build step before any dependency is installed.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Distributions present in requirements.lock that are deliberately NOT in the
# Linux RUNTIME manifest. Each entry carries its reason so an unexplained
# exclusion cannot be added silently.
RUNTIME_EXCLUSIONS: dict[str, str] = {
    "colorama": "Windows-only click dependency; not needed on Linux",
    # dev/test tooling: present in requirements-linux-dev.txt, not the runtime image
    "pytest": "dev-only test runner (requirements-linux-dev.txt)",
    "hypothesis": "dev-only property testing (requirements-linux-dev.txt)",
    "ruff": "dev-only linter (requirements-linux-dev.txt)",
    "iniconfig": "pytest transitive dev dependency (requirements-linux-dev.txt)",
    "pluggy": "pytest transitive dev dependency (requirements-linux-dev.txt)",
    "attrs": "hypothesis transitive dev dependency (requirements-linux-dev.txt)",
    "sortedcontainers": "hypothesis transitive dev dependency (requirements-linux-dev.txt)",
    "packaging": "pytest/dev transitive dependency (requirements-linux-dev.txt)",
}

_PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([^\s;#]+)")


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_pins(text: str) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-r"):
            continue
        match = _PIN_RE.match(line)
        if match:
            pins[_normalize(match.group(1))] = match.group(2)
    return pins


def parse_pyproject(path: Path) -> dict[str, str]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    deps = data.get("project", {}).get("dependencies", [])
    pins: dict[str, str] = {}
    for dep in deps:
        match = _PIN_RE.match(dep.strip())
        if match:
            pins[_normalize(match.group(1))] = match.group(2)
    return pins


def main() -> int:
    pyproject = parse_pyproject(REPO_ROOT / "pyproject.toml")
    lock = parse_pins((REPO_ROOT / "requirements.lock").read_text(encoding="utf-8"))
    linux = parse_pins((REPO_ROOT / "deploy" / "requirements-linux.txt").read_text(encoding="utf-8"))
    linux_dev = parse_pins((REPO_ROOT / "deploy" / "requirements-linux-dev.txt").read_text(encoding="utf-8"))

    errors: list[str] = []

    # 1. direct pins (named in pyproject) must agree across pyproject, lock, and
    #    whichever Linux manifest they belong to.
    linux_union = {**linux, **linux_dev}
    exclusions = {_normalize(name): why for name, why in RUNTIME_EXCLUSIONS.items()}
    for name, version in sorted(pyproject.items()):
        if name not in lock:
            errors.append(f"direct dep {name} is in pyproject but absent from requirements.lock")
        elif lock[name] != version:
            errors.append(f"direct dep {name}: pyproject=={version} but lock=={lock[name]}")
        if name not in linux_union:
            errors.append(f"direct dep {name} is in pyproject but absent from both Linux manifests")
        elif linux_union[name] != version:
            errors.append(f"direct dep {name}: pyproject=={version} but linux=={linux_union[name]}")

    # 2. every transitive distribution in the lock must be covered by a Linux
    #    manifest unless it is a documented exclusion.
    for name, version in sorted(lock.items()):
        if name in exclusions:
            if name in linux:
                errors.append(f"{name} is marked a RUNTIME exclusion but appears in requirements-linux.txt")
            continue
        if name not in linux_union:
            errors.append(f"lock distribution {name}=={version} is covered by no Linux manifest and is not an excluded entry")
        elif linux_union[name] != version:
            errors.append(f"version drift for {name}: lock=={version} but linux=={linux_union[name]}")

    # 3. no Linux manifest entry should be absent from the lock (a phantom pin).
    for name, version in sorted(linux_union.items()):
        if name not in lock:
            errors.append(f"Linux manifest pins {name}=={version} which is absent from requirements.lock")

    if errors:
        print("requirements drift detected:", file=sys.stderr)
        for line in errors:
            print(f"  - {line}", file=sys.stderr)
        return 1

    print(
        f"requirements in sync: {len(pyproject)} direct pins, "
        f"{len(linux)} runtime pins, {len(linux_union)} total Linux pins, "
        f"{len(exclusions)} documented exclusions."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
