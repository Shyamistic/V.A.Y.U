"""`python -m monsoon_grid` entry point."""

from __future__ import annotations

import argparse
import sys


def _inventory_without_optional_dependencies() -> int:
    from pathlib import Path
    from .provenance import inventory
    parser = argparse.ArgumentParser(prog="monsoon_grid inventory")
    parser.add_argument("--output", required=True)
    parser.add_argument("--compare-to", default="")
    args = parser.parse_args(sys.argv[2:])
    result = inventory(Path(args.output), compare_to=Path(args.compare_to) if args.compare_to else None)
    print({"output": args.output, "protected_path_count": len(result["protected_paths"]), "comparison": result["comparison"]})
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "inventory":
        raise SystemExit(_inventory_without_optional_dependencies())
    from .cli import main
    raise SystemExit(main())
