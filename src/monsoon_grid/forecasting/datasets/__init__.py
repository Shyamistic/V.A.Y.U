"""Dataset access layer: canonical schema, content-addressed cache, loaders.

Loaders normalize every provider to one record schema so evaluation is
dataset-agnostic. Units are converted exactly once, at ingestion (invariant 9).
No synthetic series is ever substituted for data that could not be retrieved;
an unobtainable dataset is reported ``obtained: false`` with a reason.
"""

from __future__ import annotations

__all__: list[str] = []
