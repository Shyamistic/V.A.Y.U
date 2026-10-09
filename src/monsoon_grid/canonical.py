"""Canonical deterministic JSON, record IDs, and replay state digests.

The serializer is deliberately independent of runtime timestamps, process IDs, and
signatures. It emits UTF-8 JSON with sorted keys, no whitespace, finite numbers
quantized with Decimal ROUND_HALF_EVEN to six places, and UTC timestamps in the
specified form.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import math
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from enum import Enum
from typing import Any, Mapping

from .errors import MonsoonGridError

_QUANTUM = Decimal("0.000001")


class CanonicalizationError(MonsoonGridError):
    code = "CANONICALIZATION_ERROR"


def _utc_text(value: dt.datetime) -> str:
    if value.tzinfo is None:
        raise CanonicalizationError("datetime must be timezone-aware UTC")
    value = value.astimezone(dt.timezone.utc)
    if value.microsecond:
        text = value.strftime("%Y-%m-%dT%H:%M:%S.%fZ").rstrip("0")
        if text.endswith(".Z"):
            text = text[:-2] + "Z"
        return text
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _number_text(value: int | float | Decimal) -> str:
    if isinstance(value, bool):
        raise CanonicalizationError("boolean is not a number")
    if isinstance(value, float) and not math.isfinite(value):
        raise CanonicalizationError("non-finite float is not canonical")
    try:
        decimal = Decimal(str(value)).quantize(_QUANTUM, rounding=ROUND_HALF_EVEN)
    except (InvalidOperation, ValueError) as exc:
        raise CanonicalizationError(f"unsupported numeric value: {value!r}") from exc
    if not decimal.is_finite():
        raise CanonicalizationError("non-finite decimal is not canonical")
    if decimal == 0:
        return "0"
    text = format(decimal, "f").rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _prepare(value: Any) -> Any:
    """Convert supported model values into canonical primitive structures."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, (int, float, Decimal)):
        return value
    if isinstance(value, dt.datetime):
        return _utc_text(value)
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, Enum):
        return _prepare(value.value)
    if dataclasses.is_dataclass(value):
        return _prepare(dataclasses.asdict(value))
    if hasattr(value, "model_dump"):
        return _prepare(value.model_dump(mode="python", exclude_none=False))
    if hasattr(value, "dict") and callable(value.dict):
        return _prepare(value.dict())
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError("canonical object keys must be strings")
            result[key] = _prepare(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_prepare(item) for item in value]
    if isinstance(value, set):
        raise CanonicalizationError("sets are unsupported because their order is not deterministic")
    raise CanonicalizationError(f"unsupported canonical value: {type(value).__name__}")


def _render(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, Decimal)):
        return _number_text(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(_render(item) for item in value) + "]"
    if isinstance(value, dict):
        items = []
        for key in sorted(value):
            items.append(json.dumps(key, ensure_ascii=False) + ":" + _render(value[key]))
        return "{" + ",".join(items) + "}"
    raise CanonicalizationError(f"unprepared canonical value: {type(value).__name__}")


def canonical_text(value: Any) -> str:
    return _render(_prepare(value))


def canonical_bytes(value: Any) -> bytes:
    return canonical_text(value).encode("utf-8")


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def record_id(value: Any) -> str:
    """Return the SHA-256 of the deterministic model payload."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="python", exclude={"record_id"})
    elif isinstance(value, dict):
        value = dict(value)
        value.pop("record_id", None)
    return sha256_hex(value)


def state_digest(*, replay_id: str, slot_index: int, portfolio_state: Any, twin_state: Any, pending_approval_state: Any, applied_event_ids: list[str]) -> str:
    payload = {
        "replay_id": replay_id,
        "slot_index": slot_index,
        "portfolio_state": portfolio_state,
        "twin_state": twin_state,
        "pending_approval_state": pending_approval_state,
        "applied_event_ids": applied_event_ids,
    }
    return sha256_hex(payload)


def canonical_equal(left: Any, right: Any) -> bool:
    return canonical_bytes(left) == canonical_bytes(right)
