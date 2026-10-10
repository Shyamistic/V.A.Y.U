r"""Optional pvlib cross-check (design §4.11). Skips cleanly unless pvlib imports.

CI NEVER depends on this. Running it requires installing pvlib+pandas into the
shared product runtime (see requirements-experiments.txt), which the design
discourages.

Command:
    & $py .\monsoon_grid\experiments\run_pvlib_crosscheck.py
"""

from __future__ import annotations

import sys


def main() -> int:
    try:
        import pvlib  # noqa: F401
    except ImportError:
        print("pvlib not importable -- cross-check SKIPPED (this is expected and fine).")
        return 0
    print("pvlib present -- a real cross-check against our solar position / POA / SAPM would run here.")
    print("See docs/forecasting.md for the comparison protocol and known limitations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
