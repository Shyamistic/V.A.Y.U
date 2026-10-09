"""Strict read-only weather-context-only VAYU boundary."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from .canonical import canonical_bytes
from .config import FIXTURE_ROOT, STORE_ROOT
from .contracts import WeatherContext
from .errors import DependencyUnavailableError, InputMissingError
from .fixtures import load_demo_weather


@dataclass(frozen=True)
class AdapterStatus:
    state: str
    source: str
    content_sha256: str | None
    reason: str


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, newurl, *_args):
        raise InputMissingError("redirects are not allowed for VAYU context reads")


class VayuContextAdapter:
    """Never imports VAYU and never returns an authoritative dispatch field."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        fixture_path: Path | None = None,
        allowlisted_url: str | None = None,
        expected_sha256: str | None = None,
        allowlisted_urls: Iterable[str] = (),
    ):
        self.enabled = enabled
        self.fixture_path = fixture_path
        self.allowlisted_url = allowlisted_url
        self.expected_sha256 = expected_sha256.lower() if expected_sha256 else None
        self.allowlisted_urls = frozenset(allowlisted_urls)
        self.status = AdapterStatus("DISABLED", "none", None, "VAYU context adapter disabled by default")

    def _verify_digest(self, raw: bytes) -> str:
        if not self.expected_sha256 or len(self.expected_sha256) != 64 or any(character not in "0123456789abcdef" for character in self.expected_sha256):
            raise InputMissingError("enabled VAYU context requires a mandatory expected SHA-256")
        digest = hashlib.sha256(raw).hexdigest()
        if digest != self.expected_sha256:
            raise InputMissingError("weather context content hash does not match expected hash")
        return digest

    def _read_local(self) -> tuple[bytes, str]:
        if self.fixture_path is None:
            raise DependencyUnavailableError("enabled VAYU context requires a configured local content-addressed JSON or allowlisted GET")
        path = self.fixture_path.resolve()
        allowed_roots = (FIXTURE_ROOT.resolve(), (STORE_ROOT / "sha256").resolve())
        if (path.suffix.lower() != ".json" and path.parent != (STORE_ROOT / "sha256").resolve()) or not any(path == root or root in path.parents for root in allowed_roots):
            raise InputMissingError("VAYU local context must be a JSON file under the configured fixture/content store")
        raw = path.read_bytes()
        return raw, str(path)

    def _read_url(self) -> tuple[bytes, str]:
        if not self.allowlisted_url or self.allowlisted_url not in self.allowlisted_urls:
            raise InputMissingError("VAYU URL is not explicitly allowlisted")
        parsed = urlparse(self.allowlisted_url)
        if parsed.scheme != "https" or parsed.query or parsed.fragment:
            raise InputMissingError("VAYU allowlisted URL must be an HTTPS GET without query or fragment")
        request = urllib.request.Request(self.allowlisted_url, method="GET", headers={"Accept": "application/json"})
        opener = urllib.request.build_opener(_NoRedirectHandler())
        try:
            with opener.open(request, timeout=5) as response:
                if response.geturl() != self.allowlisted_url:
                    raise InputMissingError("VAYU URL response origin changed")
                return response.read(), self.allowlisted_url
        except (OSError, urllib.error.URLError) as exc:
            raise InputMissingError(f"VAYU weather context GET failed: {exc}") from exc

    def read(self) -> WeatherContext:
        if not self.enabled:
            raw = (FIXTURE_ROOT / "demo_weather_context.json").read_bytes()
            weather = load_demo_weather()
            self.status = AdapterStatus("DISABLED", "checked-in-weather-context", hashlib.sha256(raw).hexdigest(), "disabled; fixture context retained for deterministic replay")
            return weather
        if self.fixture_path is not None:
            raw, source = self._read_local()
        elif self.allowlisted_url is not None:
            raw, source = self._read_url()
        else:
            raise DependencyUnavailableError("enabled VAYU context requires a configured local content-addressed JSON or allowlisted GET")
        digest = self._verify_digest(raw)
        try:
            value: Any = json.loads(raw.decode("utf-8"))
            weather = WeatherContext.model_validate_json(raw)
        except Exception as exc:
            raise InputMissingError(f"weather context is not a strict WeatherContext: {exc}") from exc
        self.status = AdapterStatus("VAYU_CONTEXT_ONLY", source, digest, "read-only context; no authoritative fields")
        return weather

    def authoritative_fields(self, weather: WeatherContext) -> dict[str, Any]:
        """The only projection permitted across the boundary."""
        return {"regime": weather.regime, "source_version": weather.source_version, "quality": weather.quality}
