#!/usr/bin/env python3
"""ROUTE_POLICY <-> real-route correspondence gate (design §8.2-§8.3, AC-7b).

Imports auth.ROUTE_POLICY and the product app READ-ONLY, enumerates app.routes,
and emits JSON of:
  (a) routes with a non-None policy that lack a require_roles dependency
      (the gaps the §8.2 api.py patch must fill), and
  (b) routes with no ROUTE_POLICY match outside the terminal default-deny entries.

Expected today: exactly nine routes in (a) — the six typed mutating routes plus
/api/deliberate, /api/multimodal/document, /api/multimodal/nowcast — and zero in
(b). The script is advisory before the patch lands (`--baseline` suppresses the
expected nine) and a gate after.

Run as a module from the repo root to avoid local path shadowing:
    python -m deploy.check_route_coverage
"""

from __future__ import annotations

import argparse
import json
import re
import sys

# The nine routes the §8.2 dependency patch must add. Until api.py is patched by
# the owning workflow, these are expected gaps, not failures.
EXPECTED_PATCH_ROUTES = [
    "POST /replays",
    "POST /replays/{replay_id}/advance",
    "POST /validate",
    "POST /planning",
    "POST /demo",
    "POST /approvals",
    "POST /api/deliberate",
    "POST /api/multimodal/document",
    "POST /api/multimodal/nowcast",
]


def _has_require_roles(route) -> bool:
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return False
    stack = [dependant]
    while stack:
        node = stack.pop()
        call = getattr(node, "call", None)
        if call is not None and getattr(call, "__name__", "") == "dependency" and getattr(call, "__module__", "").endswith("auth"):
            return True
        if call is not None and getattr(call, "__qualname__", "").startswith("require_roles"):
            return True
        stack.extend(getattr(node, "dependencies", []) or [])
    return False


# FastAPI framework routes (docs/openapi/redoc) are not product routes and are
# governed by the safe-method default-deny (viewer). They are excluded from the
# coverage report; the SecurityHeaders/Auth stack still default-denies them.
_FRAMEWORK_PATHS = {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}


def analyze() -> dict:
    from monsoon_grid import auth
    from monsoon_grid.api import app

    policies = auth.ROUTE_POLICY
    terminal_unsafe, terminal_safe = policies[-2], policies[-1]

    mutating_missing: list[str] = []
    unmatched_mutating: list[str] = []

    for route in app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if not methods or path is None or path in _FRAMEWORK_PATHS:
            continue
        for method in sorted(methods):
            if method in {"HEAD", "OPTIONS"}:
                continue
            policy = auth.policy_for(method, path)
            label = f"{method} {path}"

            is_mutating = method not in {"GET", "HEAD", "OPTIONS"}

            # A mutating route whose only match is the terminal default-deny entry
            # is a genuine policy gap (no explicit ROUTE_POLICY row). Safe-method
            # routes falling to the viewer default-deny are intended, not a drift.
            if is_mutating and policy is terminal_unsafe:
                unmatched_mutating.append(label)

            # Mutating routes with an explicit non-None policy that still lack a
            # require_roles dependency are the §8.2 patch gaps.
            if is_mutating and policy.required_roles and policy.required_roles != frozenset({"__ui__"}):
                if not _has_require_roles(route):
                    mutating_missing.append(label)

    return {
        "routes_missing_require_roles": sorted(set(mutating_missing)),
        "routes_unmatched": sorted(set(unmatched_mutating)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true", help="suppress the expected nine patch routes")
    args = parser.parse_args()

    result = analyze()
    missing = result["routes_missing_require_roles"]

    if args.baseline:
        result["routes_missing_require_roles"] = [r for r in missing if r not in EXPECTED_PATCH_ROUTES]

    print(json.dumps(result, indent=2, sort_keys=True))

    # A drift between the policy table and the real route set is a failure: any
    # unmatched MUTATING route (no explicit ROUTE_POLICY row), or a mutating gap
    # outside the expected nine.
    extra_gaps = sorted(set(missing) - set(EXPECTED_PATCH_ROUTES))
    if result["routes_unmatched"] or extra_gaps:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
