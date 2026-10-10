"""MonsoonGrid Phase 2 forecasting experiment harness and runnable studies.

Each experiment has one documented PowerShell command (see ``README.md``) and
emits a machine-readable artifact under ``results/<experiment>/<run_id>.json``.
No experiment fabricates data: an unobtainable dataset is reported
``obtained: false`` with a reason and its conclusions are withheld.
"""

from __future__ import annotations

__all__: list[str] = []
