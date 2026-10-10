#!/usr/bin/env python
"""Cloud Build vulnerability-scan gate (design §10.3).

Reads the JSON output of `gcloud artifacts docker images scan` and exits
non-zero when any finding at or above the given severity has a fix available.
The severity threshold is a Cloud Build substitution, so it is a reviewable
policy rather than a hidden constant.

Usage: python deploy/vuln_gate.py <scan.json> <SEVERITY>
"""

from __future__ import annotations

import json
import sys

_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: vuln_gate.py <scan.json> <SEVERITY>")
        return 2
    scan_path, severity = argv
    threshold = _ORDER.get(severity.upper(), _ORDER["HIGH"])
    with open(scan_path, encoding="utf-8") as handle:
        data = json.load(handle)
    findings = data.get("vulnerabilities", data if isinstance(data, list) else [])
    blocking = [
        v for v in findings
        if _ORDER.get(str(v.get("effectiveSeverity", v.get("severity", "LOW"))).upper(), 0) >= threshold
        and v.get("fixAvailable")
    ]
    if blocking:
        print(f"{len(blocking)} fixable finding(s) at or above {severity}")
        return 1
    print("no blocking vulnerabilities")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
