"""Test bootstrap.

Ensures the product root is importable so tests that reference the repo-root
``deploy`` package (deployment tooling owned by the deployment workflow) resolve
under the canonical two-entry PYTHONPATH (src;runtime/site-packages). This adds a
path entry only; it does not alter the shipped PYTHONPATH contract.
"""

from __future__ import annotations

import sys
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[1]
if str(PRODUCT_ROOT) not in sys.path:
    sys.path.insert(0, str(PRODUCT_ROOT))
