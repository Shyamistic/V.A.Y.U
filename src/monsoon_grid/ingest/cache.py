"""Content-hash response cache for weather ingest (under runtime/cache/ by default).

Bodies are base64 so arbitrary bytes round-trip. On read the stored hash is
re-verified against the body; a mismatch raises CacheCorruptError and the entry
is never used.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

from ..errors import CacheCorruptError


class ResponseCache:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, source_id: str, cache_key: str) -> Path:
        return self.root / source_id / f"{cache_key}.json"

    def get(self, source_id: str, cache_key: str) -> dict[str, Any] | None:
        path = self._path(source_id, cache_key)
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        body = base64.b64decode(raw["body_base64"])
        if hashlib.sha256(body).hexdigest() != raw["response_content_sha256"]:
            raise CacheCorruptError(f"cache entry hash mismatch: {path}", code="INGEST_CACHE_CORRUPT")
        return {
            "body": body,
            "retrieved_at_utc": raw["retrieved_at_utc"],
            "response_content_sha256": raw["response_content_sha256"],
            "url": raw["url"],
            "source_attribution": raw.get("source_attribution"),
        }

    def put(self, source_id: str, cache_key: str, *, url: str, request: dict[str, Any], retrieved_at: dt.datetime, body: bytes, content_sha256: str, source_attribution: str) -> Path:
        path = self._path(source_id, cache_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cache_version": 1,
            "source_id": source_id,
            "url": url,
            "request": request,
            "retrieved_at_utc": retrieved_at.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "response_content_sha256": content_sha256,
            "source_attribution": source_attribution,
            "body_base64": base64.b64encode(body).decode("ascii"),
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return path
