"""Content-addressed, offline-first HTTP cache (design §8.5).

Payloads are stored at ``data/cache/<sha256-of-request>/payload`` with a sidecar
``meta.json``. Streaming download with an explicit redirect policy and byte-counted
size caps. Offline-first: every loader checks the cache before the network;
``MONSOON_GRID_FORECASTING_OFFLINE=1`` forbids the network entirely.

``httpx`` is imported lazily inside the fetch path so importing this module (and
hence the package) does not pull in httpx.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from ..errors import CacheIntegrityError, DatasetRequestRejected, DatasetTooLarge, DatasetUnavailable

OFFLINE_ENV = "MONSOON_GRID_FORECASTING_OFFLINE"
_DEFAULT_PER_FILE_CAP = 150 * 1024 * 1024
_DEFAULT_PER_DATASET_CAP = 300 * 1024 * 1024
_CHUNK = 1 << 16

# Content types that are never a dataset payload. A record landing page or an error
# page is served as HTML; accepting it as an "obtained" dataset would make the fetch
# manifest assert an acquisition it did not achieve (design §8.1/§8.5: the manifest is
# itself an evidence artifact and must only claim what was actually received).
_REJECTED_CONTENT_TYPES = ("text/html", "application/xhtml+xml")


def request_key(url: str, params: dict | None) -> str:
    payload = json.dumps({"url": url, "params": params or {}}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class CacheEntry:
    key: str
    payload_path: Path
    meta: dict

    @property
    def sha256(self) -> str:
        return self.meta["payload_sha256"]

    def read_bytes(self) -> bytes:
        data = self.payload_path.read_bytes()
        if _sha256_bytes(data) != self.meta["payload_sha256"]:
            raise CacheIntegrityError(f"cached payload digest drift for {self.key}")
        return data


class ContentAddressedCache:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _dir(self, key: str) -> Path:
        return self.root / key

    def is_offline(self) -> bool:
        return os.environ.get(OFFLINE_ENV) == "1"

    def get(self, key: str) -> CacheEntry | None:
        d = self._dir(key)
        meta_path = d / "meta.json"
        payload_path = d / "payload"
        if meta_path.exists() and payload_path.exists():
            meta = json.loads(meta_path.read_text())
            return CacheEntry(key=key, payload_path=payload_path, meta=meta)
        return None

    def fetch(
        self,
        url: str,
        *,
        params: dict | None = None,
        per_file_cap: int = _DEFAULT_PER_FILE_CAP,
        max_redirects: int = 5,
        min_bytes: int = 1,
        reject_content_types: tuple[str, ...] = _REJECTED_CONTENT_TYPES,
    ) -> CacheEntry:
        """Return a cached payload, downloading it on a miss.

        ``min_bytes`` is the smallest payload (in bytes) that may be accepted as a
        genuine dataset: a zero-length body or a payload below the per-source
        plausible floor is rejected as :class:`DatasetUnavailable` rather than
        stored and reported as obtained (design §8.1 pins the real file sizes).
        ``reject_content_types`` rejects responses whose ``Content-Type`` is a
        landing/error page (HTML) instead of the dataset payload. A cached entry is
        only returned if it clears the same floor, so a bad payload cached by an
        earlier build is not silently re-reported as obtained.
        """
        if min_bytes < 1:
            min_bytes = 1
        key = request_key(url, params)
        existing = self.get(key)
        if existing is not None:
            # Validate integrity on read.
            existing.read_bytes()
            self._assert_plausible(existing.meta, url, min_bytes, reject_content_types)
            return existing
        if self.is_offline():
            raise DatasetUnavailable(f"offline cache miss for {url}", recoverable=True)
        return self._download(url, params, key, per_file_cap, max_redirects, min_bytes, reject_content_types)

    @staticmethod
    def _assert_plausible(meta: dict, url: str, min_bytes: int, reject_content_types: tuple[str, ...]) -> None:
        observed = int(meta.get("observed_length") or 0)
        content_type = (meta.get("content_type") or "").split(";", 1)[0].strip().lower()
        if content_type and content_type in reject_content_types:
            raise DatasetUnavailable(
                f"{url} returned content-type {content_type!r} (landing/error page, not a dataset payload)",
                recoverable=True,
            )
        if observed == 0:
            raise DatasetUnavailable(f"{url} returned a zero-length body", recoverable=True)
        if observed < min_bytes:
            raise DatasetUnavailable(
                f"{url} payload {observed} bytes is below the plausible-dataset floor {min_bytes} bytes "
                "(likely a landing/error page, not the dataset)",
                recoverable=True,
            )

    def _download(
        self, url, params, key, per_file_cap, max_redirects, min_bytes=1, reject_content_types=_REJECTED_CONTENT_TYPES
    ) -> CacheEntry:
        import httpx  # lazy import

        d = self._dir(key)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "payload.tmp"
        written = 0
        hasher = hashlib.sha256()
        redirect_chain: list[str] = []
        attempts = 0
        backoff = [1.0, 2.0, 4.0]
        while True:
            attempts += 1
            try:
                with httpx.Client(follow_redirects=True, max_redirects=max_redirects, timeout=60.0) as client:
                    with client.stream("GET", url, params=params) as resp:
                        redirect_chain = [str(h.url) for h in resp.history]
                        if resp.status_code in (429,) or resp.status_code >= 500:
                            raise httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
                        if resp.status_code >= 400:
                            raise DatasetRequestRejected(
                                f"HTTP {resp.status_code} for {url}", recoverable=True
                            )
                        with open(tmp, "wb") as fh:
                            for chunk in resp.iter_bytes(_CHUNK):
                                written += len(chunk)
                                if written > per_file_cap:
                                    fh.close()
                                    tmp.unlink(missing_ok=True)
                                    raise DatasetTooLarge(
                                        f"{url} exceeds per-file cap {per_file_cap} bytes"
                                    )
                                hasher.update(chunk)
                                fh.write(chunk)
                        final_url = str(resp.url)
                        declared = resp.headers.get("content-length")
                        content_type = resp.headers.get("content-type")
                        status = resp.status_code
                break
            except httpx.HTTPStatusError:
                if attempts <= len(backoff):
                    continue
                raise DatasetUnavailable(f"{url} failed after {attempts} attempts", retryable=True, recoverable=True)
            except httpx.TransportError as exc:
                if attempts <= len(backoff):
                    continue
                raise DatasetUnavailable(f"transport error for {url}: {exc}", retryable=True, recoverable=True)

        digest = hasher.hexdigest()
        meta = {
            "url": url,
            "params": params or {},
            "final_url": final_url,
            "redirect_chain": redirect_chain,
            "declared_length": int(declared) if declared else None,
            "observed_length": written,
            "content_type": content_type,
            "payload_sha256": digest,
            "http_status": status,
        }
        # Gate BEFORE committing the payload/meta to the content-addressed store, so a
        # landing page or an empty/undersized body is never recorded as an obtained
        # dataset. Delete the partial download rather than caching it.
        try:
            self._assert_plausible(meta, url, min_bytes, reject_content_types)
        except DatasetUnavailable:
            tmp.unlink(missing_ok=True)
            raise

        payload = d / "payload"
        os.replace(tmp, payload)
        (d / "meta.json").write_text(json.dumps(meta, sort_keys=True, indent=2))
        return CacheEntry(key=key, payload_path=payload, meta=meta)


__all__ = ["ContentAddressedCache", "CacheEntry", "request_key", "OFFLINE_ENV"]
