"""MonsoonGrid authentication, authorization, and request integrity (design §5).

Answers "who is this and may they do this". Self-contained: stdlib + cryptography
+ httpx + starlette/fastapi + pydantic + the pure product helpers canonical /
errors / contracts. Imports nothing from api/cli/planner/twin/validator.

The highest-severity control lives here: authorize_approval() derives the
recorded actor_id from the VERIFIED principal only, never from request content,
and binds the approval to a canonical SHA-256 digest over eight inputs so an
authorization cannot be transplanted to another decision/state/action/actor.

Hand-rolled RS256 verification is used deliberately (no JWT library is in the
frozen dependency set): the accepted risk (algorithm/key confusion) is closed by
a single-element RS256 allowlist, a mandatory kid, and verifying the signature
before any claim is read.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Mapping, Protocol, Sequence

import httpx
from pydantic import BaseModel, ConfigDict

from . import observability as obs
from . import tenancy
from .canonical import sha256_hex
from .contracts import HASH_RE, ID_RE
from .errors import MonsoonGridError

AuthMode = Literal["production", "local_dev"]
Role = Literal["viewer", "operator", "approver", "auditor", "admin"]
_ALL_ROLES: frozenset[str] = frozenset({"viewer", "operator", "approver", "auditor", "admin"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# §5.1 Configuration and fail-closed startup
# --------------------------------------------------------------------------- #


class AuthConfigError(MonsoonGridError):
    code = "AUTH_CONFIG_INVALID"


class AuthError(MonsoonGridError):
    """Base for runtime auth rejections carrying an HTTP status + audit reason."""

    http_status = 401

    def __init__(self, message: str, *, code: str, reason: str, http_status: int = 401, retryable: bool = False) -> None:
        super().__init__(message, code=code, retryable=retryable)
        self.reason = reason
        self.http_status = http_status


class AuthRequired(AuthError):
    def __init__(self, reason: str = "missing") -> None:
        super().__init__("authentication is required", code="AUTH_REQUIRED", reason=reason, http_status=401)


class AuthTokenMalformed(AuthError):
    def __init__(self, reason: str = "malformed") -> None:
        super().__init__("the Authorization token is malformed", code="AUTH_TOKEN_MALFORMED", reason=reason, http_status=401)


class AuthTokenInvalid(AuthError):
    def __init__(self, reason: str) -> None:
        super().__init__("the Authorization token is invalid", code="AUTH_TOKEN_INVALID", reason=reason, http_status=401)


class AuthKeysUnavailable(AuthError):
    def __init__(self) -> None:
        super().__init__("authentication keys are temporarily unavailable", code="AUTH_KEYS_UNAVAILABLE", reason="keys_unavailable", http_status=503, retryable=True)


class AuthzDenied(AuthError):
    def __init__(self, required_role: str = "") -> None:
        super().__init__("you are not authorized for this action", code="AUTHZ_DENIED", reason="authz_denied", http_status=403)
        self.required_role = required_role


@dataclass(frozen=True)
class BucketSpec:
    capacity: int
    refill_per_second: float
    scope: Literal["principal", "ip"]


_DEFAULT_RATE_LIMITS: dict[str, BucketSpec] = {
    "read|p": BucketSpec(60, 10.0, "principal"),
    "read|i": BucketSpec(120, 20.0, "ip"),
    "mutating|p": BucketSpec(20, 2.0, "principal"),
    "mutating|i": BucketSpec(40, 4.0, "ip"),
    "mutating_heavy|p": BucketSpec(3, 0.2, "principal"),
    "mutating_heavy|i": BucketSpec(6, 0.4, "ip"),
    "ops|p": BucketSpec(120, 20.0, "principal"),
    "ops|i": BucketSpec(240, 40.0, "ip"),
    "auth_fail|i": BucketSpec(10, 0.5, "ip"),
}

_DEFAULT_BODY_LIMITS: dict[str, int] = {
    "read": 8 * 1024,
    "mutating": 1024 * 1024,
    "mutating_heavy": 64 * 1024,
    "ops": 1024,
}


@dataclass(frozen=True)
class AuthConfig:
    env: Literal["production", "development"]
    mode: AuthMode
    audience: str | None
    allowed_issuers: tuple[str, ...]
    jwks_uri: str
    jwks_ttl_seconds: int
    jwks_hard_max_age_seconds: int
    jwks_timeout_seconds: float
    jwks_max_bytes: int
    clock_skew_seconds: int
    role_map_source: str | None
    dev_bearer_token: str | None
    cors_allow_origins: tuple[str, ...]
    hsts_max_age_seconds: int
    default_body_limit_bytes: int
    body_limit_overrides: Mapping[str, int]
    read_timeout_seconds: float
    mutating_timeout_seconds: float
    heavy_timeout_seconds: float
    rate_limits: Mapping[str, BucketSpec]
    require_tenant_claim: bool
    service_account_subjects: frozenset[str]
    gcp_project_id: str | None
    dev_roles: tuple[str, ...] = ()
    dev_subject: str = "dev-operator"
    trust_forwarded_for: bool = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "AuthConfig":
        env_map = dict(os.environ if environ is None else environ)

        env = env_map.get("MONSOON_GRID_ENV", "development").strip().lower()
        if env not in {"production", "development"}:
            raise AuthConfigError("MONSOON_GRID_ENV must be 'production' or 'development'")

        mode = env_map.get("MONSOON_GRID_AUTH_MODE", "local_dev").strip().lower()
        if mode not in {"production", "local_dev"}:
            raise AuthConfigError("MONSOON_GRID_AUTH_MODE must be 'production' or 'local_dev'")

        if env == "production" and mode == "local_dev":
            raise AuthConfigError("local_dev auth mode is forbidden when MONSOON_GRID_ENV=production")

        dev_token = env_map.get("MONSOON_GRID_DEV_BEARER_TOKEN") or None
        if env == "production" and dev_token:
            raise AuthConfigError("a dev bearer token must not be set when MONSOON_GRID_ENV=production")

        audience = env_map.get("MONSOON_GRID_AUTH_AUDIENCE") or None
        role_map_source = env_map.get("MONSOON_GRID_ROLE_MAP") or None

        if env == "production":
            if not audience:
                raise AuthConfigError("MONSOON_GRID_AUTH_AUDIENCE is required in production")
            if not role_map_source:
                raise AuthConfigError("MONSOON_GRID_ROLE_MAP is required in production")
            if mode != "production":
                raise AuthConfigError("MONSOON_GRID_AUTH_MODE must be 'production' in production")

        issuers_raw = env_map.get("MONSOON_GRID_AUTH_ISSUERS", "https://accounts.google.com,accounts.google.com")
        allowed_issuers = tuple(s.strip() for s in issuers_raw.split(",") if s.strip())

        jwks_uri = env_map.get("MONSOON_GRID_AUTH_JWKS_URI", "https://www.googleapis.com/oauth2/v3/certs")
        if not jwks_uri.startswith("https://"):
            raise AuthConfigError("MONSOON_GRID_AUTH_JWKS_URI must be https://")

        cors_raw = env_map.get("MONSOON_GRID_CORS_ORIGINS", "")
        cors_origins = tuple(s.strip() for s in cors_raw.split(",") if s.strip())
        for origin in cors_origins:
            if not origin.startswith("https://"):
                raise AuthConfigError("every CORS origin must be an absolute https:// origin")

        sa_raw = env_map.get("MONSOON_GRID_SERVICE_ACCOUNT_SUBJECTS", "")
        sa_subjects = []
        for entry in (s.strip() for s in sa_raw.split(",") if s.strip()):
            if not ID_RE.fullmatch(entry):
                raise AuthConfigError("each service-account subject must match ID_RE")
            sa_subjects.append(entry)

        dev_roles_raw = env_map.get("MONSOON_GRID_DEV_ROLES", "viewer,operator,approver,auditor,admin")
        dev_roles = tuple(s.strip() for s in dev_roles_raw.split(",") if s.strip())

        return cls(
            env=env,  # type: ignore[arg-type]
            mode=mode,  # type: ignore[arg-type]
            audience=audience,
            allowed_issuers=allowed_issuers,
            jwks_uri=jwks_uri,
            jwks_ttl_seconds=int(env_map.get("MONSOON_GRID_JWKS_TTL_SECONDS", "3600")),
            jwks_hard_max_age_seconds=int(env_map.get("MONSOON_GRID_JWKS_HARD_MAX_AGE_SECONDS", "86400")),
            jwks_timeout_seconds=float(env_map.get("MONSOON_GRID_JWKS_TIMEOUT_SECONDS", "3.0")),
            jwks_max_bytes=int(env_map.get("MONSOON_GRID_JWKS_MAX_BYTES", str(131072))),
            clock_skew_seconds=int(env_map.get("MONSOON_GRID_CLOCK_SKEW_SECONDS", "60")),
            role_map_source=role_map_source,
            dev_bearer_token=dev_token,
            cors_allow_origins=cors_origins,
            hsts_max_age_seconds=int(env_map.get("MONSOON_GRID_HSTS_MAX_AGE_SECONDS", "31536000")),
            default_body_limit_bytes=int(env_map.get("MONSOON_GRID_DEFAULT_BODY_LIMIT_BYTES", str(64 * 1024))),
            body_limit_overrides=dict(_DEFAULT_BODY_LIMITS),
            read_timeout_seconds=float(env_map.get("MONSOON_GRID_READ_TIMEOUT_SECONDS", "30")),
            mutating_timeout_seconds=float(env_map.get("MONSOON_GRID_MUTATING_TIMEOUT_SECONDS", "60")),
            heavy_timeout_seconds=float(env_map.get("MONSOON_GRID_HEAVY_TIMEOUT_SECONDS", "295")),
            rate_limits=dict(_DEFAULT_RATE_LIMITS),
            require_tenant_claim=env_map.get("MONSOON_GRID_REQUIRE_TENANT_CLAIM", "0") == "1",
            service_account_subjects=frozenset(sa_subjects),
            gcp_project_id=env_map.get("MONSOON_GRID_GCP_PROJECT") or None,
            dev_roles=dev_roles,
            dev_subject=env_map.get("MONSOON_GRID_DEV_SUBJECT", "dev-operator"),
            trust_forwarded_for=env_map.get("MONSOON_GRID_TRUST_FORWARDED_FOR", "0") == "1",
        )


# --------------------------------------------------------------------------- #
# §5.2 Principal model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Principal:
    actor_id: str
    subject: str
    issuer: str
    auth_method: Literal["google_oidc", "local_static_bearer"]
    roles: frozenset[str]
    tenant_id: str
    token_fingerprint: str
    expires_at: datetime | None
    email: str | None
    email_verified: bool


def principal_actor_id(prefix: str, subject: str) -> str:
    actor = f"{prefix}:{subject}"
    if not ID_RE.fullmatch(actor):
        raise AuthConfigError("derived actor_id does not satisfy the product ID pattern")
    return actor


def token_fingerprint(raw_token: str) -> str:
    import hashlib

    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# §5.5 RBAC role map
# --------------------------------------------------------------------------- #


class RoleMap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    by_subject: dict[str, list[Role]] = {}
    by_email: dict[str, list[Role]] = {}
    default_roles: list[Role] = []
    allow_role_overlap: bool = False
    tenant_by_subject: dict[str, str] = {}

    def validate_for(self, env: str) -> None:
        if env == "production":
            if self.default_roles:
                raise AuthConfigError("default_roles must be empty in production")
            if self.allow_role_overlap:
                raise AuthConfigError("allow_role_overlap must be false in production")
            for subject, roles in self.by_subject.items():
                role_set = set(roles)
                if "operator" in role_set and "approver" in role_set:
                    raise AuthConfigError(f"principal {subject} may not hold both operator and approver")
            for email, roles in self.by_email.items():
                role_set = set(roles)
                if "operator" in role_set and "approver" in role_set:
                    raise AuthConfigError(f"principal {email} may not hold both operator and approver")

    def roles_for(self, *, subject: str, email: str | None, email_verified: bool) -> tuple[frozenset[str], str | None]:
        if subject in self.by_subject:
            return frozenset(self.by_subject[subject]), None
        if email and email_verified and email in self.by_email:
            return frozenset(self.by_email[email]), None
        if email and not email_verified and email in self.by_email:
            return frozenset(self.default_roles), "email_unverified"
        return frozenset(self.default_roles), "unmapped" if not self.default_roles else None


def load_role_map(config: AuthConfig) -> RoleMap:
    source = config.role_map_source
    if not source:
        role_map = RoleMap()
    elif source.startswith("file:"):
        path = source[len("file:"):]
        role_map = RoleMap.model_validate_json(open(path, encoding="utf-8").read())
    else:
        try:
            role_map = RoleMap.model_validate_json(source)
        except Exception as exc:  # pydantic ValidationError or JSON error
            raise AuthConfigError(f"role map is not valid: {exc}") from exc
    role_map.validate_for(config.env)
    return role_map


# --------------------------------------------------------------------------- #
# §5.4 JWKS cache
# --------------------------------------------------------------------------- #


@dataclass
class CachedJwks:
    keys: dict[str, Any]
    fetched_at: datetime
    etag: str | None


class JwksCache:
    def __init__(
        self,
        *,
        config: AuthConfig,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._config = config
        self._transport = transport
        self._clock = clock
        self._lock = threading.Lock()
        self._cache: CachedJwks | None = None
        self._last_negative_fetch: dict[str, float] = {}

    def _client(self) -> httpx.Client:
        return httpx.Client(transport=self._transport, timeout=self._config.jwks_timeout_seconds)

    def _parse(self, body: bytes) -> dict[str, Any]:
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers

        if len(body) > self._config.jwks_max_bytes:
            raise AuthKeysUnavailable()
        document = json.loads(body)
        if not isinstance(document, dict) or not isinstance(document.get("keys"), list):
            raise AuthKeysUnavailable()
        keys: dict[str, Any] = {}
        for jwk in document["keys"]:
            if not isinstance(jwk, dict) or jwk.get("kty") != "RSA":
                continue
            if jwk.get("alg") not in (None, "RS256"):
                continue
            kid = jwk.get("kid")
            if not isinstance(kid, str) or not kid:
                continue
            try:
                n = int.from_bytes(base64.urlsafe_b64decode(_pad(jwk["n"])), "big")
                e = int.from_bytes(base64.urlsafe_b64decode(_pad(jwk["e"])), "big")
                keys[kid] = RSAPublicNumbers(e, n).public_key()
            except Exception:
                continue
        return keys

    def refresh(self, *, force: bool = False) -> None:
        with self._lock:
            now = self._clock()
            if not force and self._cache is not None:
                age = (now - self._cache.fetched_at).total_seconds()
                if age < self._config.jwks_ttl_seconds:
                    return
            headers = {}
            if self._cache is not None and self._cache.etag:
                headers["If-None-Match"] = self._cache.etag
            try:
                with self._client() as client:
                    response = client.get(self._config.jwks_uri, headers=headers)
            except Exception:
                self._on_refresh_failure("fetch_error", now)
                return
            if response.status_code == 304 and self._cache is not None:
                self._cache.fetched_at = now
                return
            if response.status_code != 200:
                self._on_refresh_failure(f"http_{response.status_code}", now)
                return
            try:
                keys = self._parse(response.content)
            except AuthKeysUnavailable:
                self._on_refresh_failure("parse_error", now)
                return
            self._cache = CachedJwks(keys=keys, fetched_at=now, etag=response.headers.get("ETag"))
            gauge = obs.default_registry().get("monsoongrid_jwks_keys")
            if gauge is not None:
                gauge.set(len(keys))  # type: ignore[attr-defined]

    def _on_refresh_failure(self, reason: str, now: datetime) -> None:
        counter = obs.default_registry().get("monsoongrid_jwks_refresh_failures_total")
        if counter is not None:
            counter.inc(reason=reason)  # type: ignore[attr-defined]
        if self._cache is not None:
            age = (now - self._cache.fetched_at).total_seconds()
            if age >= self._config.jwks_hard_max_age_seconds:
                self._cache = None

    def get(self, kid: str) -> Any | None:
        with self._lock:
            cache = self._cache
            now = self._clock()
        if cache is None:
            self.refresh(force=True)
            with self._lock:
                cache = self._cache
        elif (now - cache.fetched_at).total_seconds() >= self._config.jwks_ttl_seconds:
            self.refresh()
            with self._lock:
                cache = self._cache
        if cache is None:
            raise AuthKeysUnavailable()
        if kid in cache.keys:
            return cache.keys[kid]
        # negative-cache throttle: at most one forced refresh per kid per ttl/4
        window = max(1.0, self._config.jwks_ttl_seconds / 4)
        monotonic = time.monotonic()
        last = self._last_negative_fetch.get(kid, 0.0)
        if monotonic - last >= window:
            self._last_negative_fetch[kid] = monotonic
            self.refresh(force=True)
            with self._lock:
                cache = self._cache
            if cache is not None and kid in cache.keys:
                return cache.keys[kid]
        return None

    def prefetch_or_raise(self) -> None:
        self.refresh(force=True)
        with self._lock:
            if self._cache is None:
                raise AuthKeysUnavailable()


def _pad(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return (value + pad).encode("ascii")


# --------------------------------------------------------------------------- #
# §5.3 Identity verification
# --------------------------------------------------------------------------- #


class IdentityVerifier(Protocol):
    def verify(self, authorization_header: str | None, *, now: datetime) -> Principal: ...


@dataclass
class _ParsedToken:
    header: dict[str, Any]
    payload: dict[str, Any]
    signing_input: bytes
    signature: bytes
    raw: str


def _split_bearer(authorization_header: str | None) -> str:
    if not authorization_header:
        raise AuthRequired("missing")
    if len(authorization_header) > 8200:
        raise AuthTokenMalformed("oversize_header")
    parts = authorization_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthTokenMalformed("scheme")
    return parts[1]


def _parse_jwt(token: str) -> _ParsedToken:
    if len(token) > 8192:
        raise AuthTokenMalformed("oversize_token")
    segments = token.split(".")
    if len(segments) != 3:
        raise AuthTokenMalformed("segments")
    header_b64, payload_b64, sig_b64 = segments
    try:
        header = json.loads(base64.urlsafe_b64decode(_pad(header_b64)))
        payload = json.loads(base64.urlsafe_b64decode(_pad(payload_b64)))
        signature = base64.urlsafe_b64decode(_pad(sig_b64))
    except Exception as exc:
        raise AuthTokenInvalid("decode") from exc
    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise AuthTokenInvalid("structure")
    return _ParsedToken(
        header=header,
        payload=payload,
        signing_input=f"{header_b64}.{payload_b64}".encode("ascii"),
        signature=signature,
        raw=token,
    )


class GoogleOidcVerifier:
    def __init__(self, *, config: AuthConfig, jwks: JwksCache, role_map: RoleMap) -> None:
        self._config = config
        self._jwks = jwks
        self._role_map = role_map

    def verify(self, authorization_header: str | None, *, now: datetime) -> Principal:
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.hashes import SHA256

        token = _split_bearer(authorization_header)
        parsed = _parse_jwt(token)

        # 3. header: single RS256 allowlist + mandatory kid
        if parsed.header.get("alg") != "RS256":
            raise AuthTokenInvalid("alg")
        kid = parsed.header.get("kid")
        if not isinstance(kid, str) or not kid or len(kid) > 128:
            raise AuthTokenInvalid("kid")

        # 4. resolve kid
        public_key = self._jwks.get(kid)
        if public_key is None:
            raise AuthTokenInvalid("unknown_kid")

        # 5. verify signature BEFORE reading any claim
        try:
            public_key.verify(parsed.signature, parsed.signing_input, padding.PKCS1v15(), SHA256())
        except Exception as exc:
            raise AuthTokenInvalid("signature") from exc

        claims = parsed.payload
        skew = self._config.clock_skew_seconds
        now_ts = now.timestamp()

        iss = claims.get("iss")
        if iss not in self._config.allowed_issuers:
            raise AuthTokenInvalid("iss")

        aud = claims.get("aud")
        audience = self._config.audience
        if isinstance(aud, list):
            if aud.count(audience) != 1:
                raise AuthTokenInvalid("aud")
        elif aud != audience:
            raise AuthTokenInvalid("aud")

        exp = claims.get("exp")
        if not isinstance(exp, (int, float)) or exp <= now_ts - skew:
            raise AuthTokenInvalid("expired")
        iat = claims.get("iat")
        if isinstance(iat, (int, float)) and iat > now_ts + skew:
            raise AuthTokenInvalid("not_yet_valid")
        nbf = claims.get("nbf")
        if isinstance(nbf, (int, float)) and nbf > now_ts + skew:
            raise AuthTokenInvalid("not_yet_valid")

        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub or len(sub) > 255:
            raise AuthTokenInvalid("sub")

        email = claims.get("email")
        email_verified = bool(claims.get("email_verified", False))
        roles, _reason = self._role_map.roles_for(subject=sub, email=email, email_verified=email_verified)

        scope = tenancy.resolve_tenant(claims=claims, role_map=self._role_map, config=self._config)
        actor_id = principal_actor_id("oidc", sub)
        return Principal(
            actor_id=actor_id,
            subject=sub,
            issuer=str(iss),
            auth_method="google_oidc",
            roles=roles,
            tenant_id=scope.tenant_id,
            token_fingerprint=token_fingerprint(token),
            expires_at=datetime.fromtimestamp(float(exp), timezone.utc),
            email=email if isinstance(email, str) else None,
            email_verified=email_verified,
        )


class StaticBearerVerifier:
    def __init__(self, *, config: AuthConfig, role_map: RoleMap) -> None:
        if config.env == "production":
            raise AuthConfigError("StaticBearerVerifier cannot be constructed in production")
        if not config.dev_bearer_token:
            raise AuthConfigError("local_dev mode requires MONSOON_GRID_DEV_BEARER_TOKEN")
        self._config = config
        self._role_map = role_map

    def verify(self, authorization_header: str | None, *, now: datetime) -> Principal:
        token = _split_bearer(authorization_header)
        if not hmac.compare_digest(token, self._config.dev_bearer_token or ""):
            raise AuthTokenInvalid("dev_token_mismatch")
        subject = self._config.dev_subject
        roles = frozenset(self._config.dev_roles)
        scope = tenancy.resolve_tenant(claims={"sub": subject}, role_map=self._role_map, config=self._config)
        return Principal(
            actor_id=principal_actor_id("local", subject),
            subject=subject,
            issuer="local-dev",
            auth_method="local_static_bearer",
            roles=roles,
            tenant_id=scope.tenant_id,
            token_fingerprint=token_fingerprint(token),
            expires_at=None,
            email=None,
            email_verified=False,
        )


def build_verifier(config: AuthConfig, role_map: RoleMap, jwks: JwksCache | None = None) -> IdentityVerifier:
    if config.mode == "production":
        return GoogleOidcVerifier(config=config, jwks=jwks or JwksCache(config=config), role_map=role_map)
    return StaticBearerVerifier(config=config, role_map=role_map)


# --------------------------------------------------------------------------- #
# §5.5 Route policy table
# --------------------------------------------------------------------------- #

RateClass = Literal["read", "mutating", "mutating_heavy", "ops"]


@dataclass(frozen=True)
class RoutePolicy:
    method_pattern: str
    path_pattern: str
    required_roles: frozenset[str] | None
    rate_class: RateClass
    body_limit_bytes: int
    timeout_seconds: float
    tenant_scoped: bool


_KiB = 1024
_MiB = 1024 * 1024


def _rp(method: str, path: str, roles: Sequence[str] | None, rate_class: RateClass, body: int, timeout: float, tenant: bool = False) -> RoutePolicy:
    return RoutePolicy(
        method_pattern=f"^{method}$",
        path_pattern="^" + path + "$",
        required_roles=None if roles is None else frozenset(roles),
        rate_class=rate_class,
        body_limit_bytes=body,
        timeout_seconds=timeout,
        tenant_scoped=tenant,
    )


_P = r"[^/]+"
ROUTE_POLICY: tuple[RoutePolicy, ...] = (
    _rp("GET", r"/_ops/healthz", None, "ops", _KiB, 5),
    _rp("GET", r"/_ops/readyz", None, "ops", _KiB, 10),
    _rp("GET", r"/_ops/metrics", ["viewer", "operator", "approver", "auditor", "admin"], "ops", _KiB, 10),
    _rp("POST", r"/_sched/fast-loop", ["operator"], "mutating_heavy", _KiB, 295),
    _rp("POST", r"/_sched/slow-loop", ["operator"], "mutating_heavy", _KiB, 295),
    _rp("GET", r"/health", None, "ops", _KiB, 10),
    _rp("GET", r"/ready", None, "ops", _KiB, 10),
    _rp("GET", r"/adapter-status", None, "ops", _KiB, 10),
    _rp("GET", r"/portfolio", ["viewer", "operator", "approver", "auditor", "admin"], "read", 8 * _KiB, 30),
    _rp("GET", rf"/state/{_P}", ["viewer", "operator", "approver", "auditor", "admin"], "read", 8 * _KiB, 30, tenant=True),
    _rp("GET", rf"/forecasts/{_P}", ["viewer", "operator", "approver", "auditor", "admin"], "read", 8 * _KiB, 30, tenant=True),
    _rp("GET", rf"/scenarios/{_P}", ["viewer", "operator", "approver", "auditor", "admin"], "read", 8 * _KiB, 30, tenant=True),
    _rp("GET", rf"/metrics/{_P}", ["viewer", "operator", "approver", "auditor", "admin"], "read", 8 * _KiB, 30, tenant=True),
    _rp("GET", rf"/events/{_P}", ["viewer", "operator", "approver", "auditor", "admin"], "read", 8 * _KiB, 30, tenant=True),
    _rp("GET", rf"/replays/{_P}", ["viewer", "operator", "approver", "auditor", "admin"], "read", 8 * _KiB, 30, tenant=True),
    _rp("GET", rf"/certificates/{_P}", ["viewer", "operator", "approver", "auditor", "admin"], "read", 8 * _KiB, 30, tenant=True),
    _rp("GET", r"/api/portfolio", ["viewer", "operator", "approver", "auditor", "admin"], "read", 8 * _KiB, 30),
    _rp("GET", r"/api/simulation/state", ["viewer", "operator", "approver", "auditor", "admin"], "read", 8 * _KiB, 30),
    _rp("GET", r"/api/agent/deliberate", ["viewer", "operator", "approver", "auditor", "admin"], "read", 8 * _KiB, 30, tenant=True),
    _rp("GET", r"/api/workspace", ["viewer", "operator", "approver", "auditor", "admin"], "read", 8 * _KiB, 30, tenant=True),
    _rp("GET", r"/api/session", ["viewer", "operator", "approver", "auditor", "admin"], "read", 8 * _KiB, 30),
    _rp("POST", r"/replays", ["operator"], "mutating_heavy", 64 * _KiB, 295),
    _rp("POST", rf"/replays/{_P}/advance", ["operator"], "mutating", _MiB, 60, tenant=True),
    _rp("POST", r"/validate", ["operator"], "mutating", _MiB, 60, tenant=True),
    _rp("POST", r"/planning", ["operator"], "mutating_heavy", 64 * _KiB, 60, tenant=True),
    _rp("POST", r"/approvals", ["approver"], "mutating", 64 * _KiB, 30, tenant=True),
    _rp("POST", r"/demo", ["operator"], "mutating_heavy", 8 * _KiB, 295),
    _rp("POST", r"/api/deliberate", ["operator"], "mutating_heavy", _MiB, 60),
    _rp("POST", r"/api/multimodal/document", ["operator"], "mutating_heavy", _MiB, 60),
    _rp("POST", r"/api/multimodal/nowcast", ["operator"], "mutating_heavy", _MiB, 60),
    # /ui and / are default-denied in production; allowed in development
    _rp("GET", r"/", ["__ui__"], "read", _KiB, 10),
    _rp("GET", r"/ui/.*", ["__ui__"], "read", _KiB, 10),
    _rp("GET", r"/ui", ["__ui__"], "read", _KiB, 10),
    # terminal default-deny
    _rp(r"(?!GET$|HEAD$|OPTIONS$).*", r".*", ["admin"], "mutating", 64 * _KiB, 30),
    _rp(r".*", r".*", ["viewer"], "read", 8 * _KiB, 30),
)


def policy_for(method: str, path: str) -> RoutePolicy:
    for policy in ROUTE_POLICY:
        if re.match(policy.method_pattern, method) and re.match(policy.path_pattern, path):
            return policy
    return ROUTE_POLICY[-1]


# --------------------------------------------------------------------------- #
# §5.6 Decision-author index + approval integrity
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DecisionAuthor:
    subject: str
    author_class: Literal["human", "service_account"]
    recorded_at: datetime


class DecisionAuthorIndex:
    def __init__(self, *, max_entries: int = 10000, ttl_seconds: float = 86400.0, clock: Callable[[], datetime] = _utcnow) -> None:
        self._entries: "OrderedDict[tuple[str, str], DecisionAuthor]" = OrderedDict()
        self._max = max_entries
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()

    def record_author(self, *, replay_id: str, decision_id: str, subject: str, author_class: str, now: datetime | None = None) -> None:
        now = now or self._clock()
        with self._lock:
            key = (replay_id, decision_id)
            self._entries[key] = DecisionAuthor(subject=subject, author_class=author_class, recorded_at=now)  # type: ignore[arg-type]
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)

    def author_of(self, *, replay_id: str, decision_id: str) -> DecisionAuthor | None:
        now = self._clock()
        with self._lock:
            author = self._entries.get((replay_id, decision_id))
            if author is None:
                return None
            if (now - author.recorded_at).total_seconds() > self._ttl:
                del self._entries[(replay_id, decision_id)]
                return None
            return author


@dataclass(frozen=True)
class ApprovalAuthorization:
    actor_id: str
    auth_method: Literal["google_oidc", "local_static_bearer"]
    token_subject: str
    token_issuer: str
    token_fingerprint: str
    separation_of_duty: Literal["verified_distinct", "unverifiable"]
    author_class: Literal["human", "service_account", "unknown"]
    approval_binding_digest: str
    recorded_at: datetime


@dataclass(frozen=True)
class ApprovalRejected:
    code: Literal[
        "APPROVAL_ROLE_DENIED",
        "APPROVAL_STATE_STALE",
        "APPROVAL_IDEMPOTENCY_REQUIRED",
        "APPROVAL_IDEMPOTENCY_CONFLICT",
        "APPROVAL_SELF_APPROVAL",
        "APPROVAL_AUTHOR_UNKNOWN",
        "APPROVAL_REASON_REQUIRED",
        "APPROVAL_DIGEST_MALFORMED",
    ]
    message: str
    retryable: bool


def authorize_approval(
    *,
    principal: Principal,
    replay_id: str,
    decision_id: str,
    expected_state_digest: str,
    observed_state_digest: str,
    idempotency_key: str,
    action: Literal["approve", "reject", "override", "escalate"],
    reason: str,
    author: DecisionAuthor | None,
    now: datetime,
    production: bool = True,
) -> ApprovalAuthorization | ApprovalRejected:
    # 1. role
    if "approver" not in principal.roles:
        return ApprovalRejected("APPROVAL_ROLE_DENIED", "approver role is required", False)
    # 2. reason
    stripped = (reason or "").strip()
    if not (1 <= len(stripped) <= 512) or any(ord(ch) < 32 and ch != " " for ch in stripped):
        return ApprovalRejected("APPROVAL_REASON_REQUIRED", "a 1..512 character reason is required", False)
    # 3. digests
    if not HASH_RE.fullmatch(expected_state_digest) or not HASH_RE.fullmatch(observed_state_digest):
        return ApprovalRejected("APPROVAL_DIGEST_MALFORMED", "state digests must be lowercase sha256 hex", False)
    if not hmac.compare_digest(expected_state_digest, observed_state_digest):
        return ApprovalRejected("APPROVAL_STATE_STALE", "approval was issued against a different state", True)
    # 4. idempotency
    if not ID_RE.fullmatch(idempotency_key) or not (8 <= len(idempotency_key) <= 128):
        return ApprovalRejected("APPROVAL_IDEMPOTENCY_REQUIRED", "an 8..128 character idempotency key is required", False)
    # 5. separation of duty
    separation: Literal["verified_distinct", "unverifiable"] = "verified_distinct"
    author_class: Literal["human", "service_account", "unknown"] = "unknown"
    if author is not None:
        if hmac.compare_digest(author.subject, principal.subject):
            return ApprovalRejected("APPROVAL_SELF_APPROVAL", "an operator may not approve their own decision", False)
        author_class = author.author_class
        separation = "verified_distinct"
    else:
        if production:
            return ApprovalRejected("APPROVAL_AUTHOR_UNKNOWN", "the decision author is unknown; cannot verify separation of duty", True)
        separation = "unverifiable"
    # 6. binding digest over exactly eight inputs
    binding = sha256_hex(
        {
            "schema": "monsoongrid-approval-binding-v1",
            "replay_id": replay_id,
            "decision_id": decision_id,
            "expected_state_digest": expected_state_digest,
            "actor_id": principal.actor_id,
            "action": action,
            "idempotency_key": idempotency_key,
            "token_subject": principal.subject,
            "token_issuer": principal.issuer,
            "auth_method": principal.auth_method,
        }
    )
    return ApprovalAuthorization(
        actor_id=principal.actor_id,
        auth_method=principal.auth_method,
        token_subject=principal.subject,
        token_issuer=principal.issuer,
        token_fingerprint=principal.token_fingerprint,
        separation_of_duty=separation,
        author_class=author_class,
        approval_binding_digest=binding,
        recorded_at=now,
    )


# --------------------------------------------------------------------------- #
# §5.7 Rate limiting
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BucketDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: float
    limit: int


class RateLimitStore(Protocol):
    def consume(self, key: str, spec: BucketSpec, *, now: float, cost: int = 1) -> BucketDecision: ...
    def peek(self, key: str, spec: BucketSpec, *, now: float) -> BucketDecision: ...


class InMemoryTokenBucketStore:
    def __init__(self, *, max_keys: int = 50_000, clock: Callable[[], float] = time.monotonic) -> None:
        self._buckets: "OrderedDict[str, tuple[float, float]]" = OrderedDict()
        self._max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()

    def _state(self, key: str, spec: BucketSpec, now: float) -> tuple[float, float]:
        tokens, last = self._buckets.get(key, (float(spec.capacity), now))
        tokens = min(float(spec.capacity), tokens + (now - last) * spec.refill_per_second)
        return tokens, now

    def consume(self, key: str, spec: BucketSpec, *, now: float, cost: int = 1) -> BucketDecision:
        with self._lock:
            tokens, ts = self._state(key, spec, now)
            if tokens >= cost:
                tokens -= cost
                allowed = True
                retry = 0.0
            else:
                allowed = False
                retry = (cost - tokens) / spec.refill_per_second if spec.refill_per_second else 60.0
            self._buckets[key] = (tokens, ts)
            self._buckets.move_to_end(key)
            while len(self._buckets) > self._max_keys:
                self._buckets.popitem(last=False)
            return BucketDecision(allowed=allowed, remaining=int(tokens), retry_after_seconds=retry, limit=spec.capacity)

    def peek(self, key: str, spec: BucketSpec, *, now: float) -> BucketDecision:
        with self._lock:
            tokens, _ = self._state(key, spec, now)
            return BucketDecision(allowed=tokens >= 1, remaining=int(tokens), retry_after_seconds=0.0, limit=spec.capacity)


# --------------------------------------------------------------------------- #
# §5.8 is_https predicate
# --------------------------------------------------------------------------- #


def is_https(request) -> bool:
    forwarded = request.headers.get("x-forwarded-proto", "")
    if forwarded.split(",")[0].strip().lower() == "https":
        return True
    return request.url.scheme == "https"


# --------------------------------------------------------------------------- #
# §5.9 Error envelope
# --------------------------------------------------------------------------- #


def auth_error_response(code: str, message: str, *, status: int, request_id: str, replay_id: str | None = None,
                        retryable: bool = False, recoverable: bool = False, extra_field_errors: Mapping[str, str] | None = None):
    from fastapi.responses import JSONResponse

    envelope = {
        "schema_version": "1.0",
        "request_id": request_id,
        "status": "error",
        "data": None,
        "error": {
            "code": code,
            "message": message,
            "field_errors": dict(extra_field_errors or {}),
            "replay_id": replay_id,
            "slot_index": None,
            "recoverable": recoverable,
            "retryable": retryable,
        },
        "replay_scope": "REPLAY",
    }
    return JSONResponse(status_code=status, content=envelope)


# --------------------------------------------------------------------------- #
# Middleware stack (§5.5-§5.10, §8.1)
# --------------------------------------------------------------------------- #


def _client_ip(request, trust_forwarded_for: bool) -> str:
    if trust_forwarded_for:
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",")[-1].strip()
    client = request.client
    return client.host if client else "unknown"


def _request_id(request) -> str:
    ctx = obs.current_context()
    if ctx is not None:
        return ctx.request_id
    return getattr(request.state, "request_id", "0" * 32)


def _metric_inc(name: str, **labels: str) -> None:
    inst = obs.default_registry().get(name)
    if inst is not None:
        try:
            inst.inc(**labels)  # type: ignore[attr-defined]
        except Exception:
            pass


class AuthMiddleware:
    """Verify identity, enforce the route policy, resolve tenant, attach the
    principal to request.state (§8.1 position 5)."""

    def __init__(self, app, *, config: AuthConfig, verifier: IdentityVerifier | None = None,
                 role_map: RoleMap | None = None, jwks: JwksCache | None = None,
                 audit: "obs.AuditLog | None" = None) -> None:
        self.app = app
        self.config = config
        self.role_map = role_map or load_role_map(config)
        self.verifier = verifier or build_verifier(config, self.role_map, jwks)
        self.audit = audit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from starlette.requests import Request

        request = Request(scope, receive=receive)
        policy = policy_for(request.method, request.url.path)
        request.state.route_policy = policy
        request.state.auth_environment = self.config.env
        request.state.route_template = _template(request.url.path)

        # UI gate: denied in production, allowed in development.
        if policy.required_roles == frozenset({"__ui__"}):
            if self.config.env == "production":
                response = auth_error_response(
                    "UI_NOT_AVAILABLE_IN_PRODUCTION",
                    "the browser UI is disabled in production; use the IAP path",
                    status=403, request_id=_request_id(request),
                )
                await response(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return

        if policy.required_roles is None:
            await self.app(scope, receive, send)
            return

        try:
            principal = self.verifier.verify(request.headers.get("authorization"), now=_utcnow())
        except AuthError as exc:
            request.state.auth_status = exc.http_status
            _metric_inc("monsoongrid_auth_failures_total", reason=exc.reason)
            self._audit_auth_failure(request, exc)
            response = auth_error_response(exc.code, exc.message, status=exc.http_status,
                                           request_id=_request_id(request), retryable=exc.retryable)
            if exc.http_status == 401:
                response.headers["WWW-Authenticate"] = "Bearer"
            await response(scope, receive, send)
            return

        if not (principal.roles & policy.required_roles):
            _metric_inc("monsoongrid_authz_denials_total", route=request.state.route_template,
                        required_role="|".join(sorted(policy.required_roles)))
            self._audit(request, "authz_denied", "denied", principal)
            response = auth_error_response("AUTHZ_DENIED", "you are not authorized for this action",
                                           status=403, request_id=_request_id(request))
            await response(scope, receive, send)
            return

        request.state.principal = principal
        ctx = obs.current_context()
        if ctx is not None:
            ctx.principal = principal.actor_id
            ctx.tenant = principal.tenant_id
        scope.setdefault("state", {})
        await self.app(scope, receive, send)

    def _audit(self, request, event_type, outcome, principal) -> None:
        if self.audit is None:
            return
        self.audit.append(
            event_type=event_type, outcome=outcome,
            principal=getattr(principal, "actor_id", None),
            tenant=getattr(principal, "tenant_id", None),
            request_id=_request_id(request), trace_id=None,
            target={"route": getattr(request.state, "route_template", request.url.path)},
        )

    def _audit_auth_failure(self, request, exc) -> None:
        if self.audit is None:
            return
        self.audit.append(
            event_type="auth_failure", outcome="denied", principal=None, tenant=None,
            request_id=_request_id(request), trace_id=None,
            detail={"reason": exc.reason},
        )


def _template(path: str) -> str:
    policy = policy_for("GET", path)
    # map concrete path to a stable templated label for metric cardinality
    for raw, label in (
        (r"/replays/[^/]+/advance", "/replays/{replay_id}/advance"),
        (r"/replays/[^/]+", "/replays/{replay_id}"),
        (r"/state/[^/]+", "/state/{replay_id}"),
        (r"/forecasts/[^/]+", "/forecasts/{replay_id}"),
        (r"/scenarios/[^/]+", "/scenarios/{replay_id}"),
        (r"/metrics/[^/]+", "/metrics/{replay_id}"),
        (r"/events/[^/]+", "/events/{replay_id}"),
        (r"/certificates/[^/]+", "/certificates/{certificate_id}"),
        (r"/ui/[^/]+", "/ui/{path}"),
    ):
        if re.fullmatch(raw, path):
            return label
    return path


class RateLimitMiddleware:
    def __init__(self, app, *, config: AuthConfig, store: RateLimitStore, stage: Literal["ip", "principal"]) -> None:
        self.app = app
        self.config = config
        self.store = store
        self.stage = stage

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from starlette.requests import Request

        request = Request(scope, receive=receive)
        policy = policy_for(request.method, request.url.path)
        rate_class = policy.rate_class
        now = time.monotonic()

        if self.stage == "ip":
            ip = _client_ip(request, self.config.trust_forwarded_for)
            spec = self.config.rate_limits.get(f"{rate_class}|i")
            if spec is not None:
                decision = self.store.consume(f"{rate_class}|i|{ip}", spec, now=now)
                if not decision.allowed:
                    _metric_inc("monsoongrid_rate_limited_total", rate_class=rate_class, scope="ip")
                    await self._reject(scope, receive, send, request, decision, "ip", rate_class)
                    return
            # auth_fail pre-check for protected routes
            auth_spec = self.config.rate_limits.get("auth_fail|i")
            charge_auth = policy.required_roles is not None and policy.required_roles != frozenset({"__ui__"})
            if charge_auth and auth_spec is not None:
                peek = self.store.peek(f"auth_fail|i|{ip}", auth_spec, now=now)
                if not peek.allowed:
                    _metric_inc("monsoongrid_rate_limited_total", rate_class="auth_fail", scope="ip")
                    await self._reject(scope, receive, send, request, peek, "ip", "auth_fail")
                    return

            captured: dict[str, int] = {}

            async def send_wrapper(message):
                if message["type"] == "http.response.start":
                    captured["status"] = message["status"]
                await send(message)

            await self.app(scope, receive, send_wrapper)
            if charge_auth and auth_spec is not None and captured.get("status") == 401:
                self.store.consume(f"auth_fail|i|{ip}", auth_spec, now=now)
            return

        # principal stage
        if policy.required_roles is None or policy.required_roles == frozenset({"__ui__"}):
            await self.app(scope, receive, send)
            return
        principal = getattr(request.state, "principal", None)
        if principal is None:
            await self.app(scope, receive, send)
            return
        spec = self.config.rate_limits.get(f"{rate_class}|p")
        if spec is not None:
            decision = self.store.consume(f"{rate_class}|p|{principal.actor_id}", spec, now=now)
            if not decision.allowed:
                _metric_inc("monsoongrid_rate_limited_total", rate_class=rate_class, scope="principal")
                await self._reject(scope, receive, send, request, decision, "principal", rate_class)
                return
        await self.app(scope, receive, send)

    async def _reject(self, scope, receive, send, request, decision, scope_name, rate_class) -> None:
        import math

        retry = int(math.ceil(decision.retry_after_seconds)) if decision.retry_after_seconds else 1
        response = auth_error_response(
            "RATE_LIMIT_EXCEEDED", "too many requests", status=429,
            request_id=_request_id(request), retryable=True,
            extra_field_errors={"retry_after_seconds": str(round(decision.retry_after_seconds, 3))},
        )
        response.headers["Retry-After"] = str(retry)
        response.headers["X-RateLimit-Limit"] = str(decision.limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, decision.remaining))
        response.headers["X-RateLimit-Reset"] = str(retry)
        response.headers["X-RateLimit-Scope"] = scope_name
        response.headers["X-RateLimit-Class"] = rate_class
        await response(scope, receive, send)


class SecurityHeadersMiddleware:
    def __init__(self, app, *, config: AuthConfig) -> None:
        self.app = app
        self.config = config
        for origin in config.cors_allow_origins:
            if origin == "*":
                raise AuthConfigError("CORS '*' with credentials is forbidden")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from starlette.requests import Request

        request = Request(scope)
        path = request.url.path
        is_ui = path == "/" or path.startswith("/ui")
        https = is_https(request)
        request_id = _request_id(request)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                def put(name: str, value: str) -> None:
                    headers.append((name.encode("latin-1"), value.encode("latin-1")))

                put("x-content-type-options", "nosniff")
                put("x-frame-options", "DENY")
                if is_ui:
                    put("content-security-policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
                else:
                    put("content-security-policy", "default-src 'none'; frame-ancestors 'none'; base-uri 'none'")
                put("referrer-policy", "no-referrer")
                put("cross-origin-opener-policy", "same-origin")
                put("cross-origin-resource-policy", "same-origin")
                put("permissions-policy", "geolocation=(), camera=(), microphone=(), usb=(), payment=()")
                put("cache-control", "no-store")
                if https:
                    put("strict-transport-security", f"max-age={self.config.hsts_max_age_seconds}; includeSubDomains")
                put("x-request-id", request_id)
            await send(message)

        await self.app(scope, receive, send_wrapper)


class BodyLimitMiddleware:
    def __init__(self, app, *, config: AuthConfig) -> None:
        self.app = app
        self.config = config

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from starlette.requests import Request

        request = Request(scope, receive=receive)
        policy = policy_for(request.method, request.url.path)
        limit = policy.body_limit_bytes

        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > limit:
                    await self._reject(scope, receive, send, request)
                    return
            except ValueError:
                pass

        seen = 0

        async def limited_receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise _BodyTooLarge()
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLarge:
            await self._reject(scope, receive, send, request)

    async def _reject(self, scope, receive, send, request) -> None:
        _metric_inc("monsoongrid_body_limit_rejections_total", route=_template(request.url.path))
        response = auth_error_response("REQUEST_TOO_LARGE", "request body exceeds the route limit",
                                       status=413, request_id=_request_id(request))
        await response(scope, receive, send)


class _BodyTooLarge(Exception):
    pass


class TimeoutMiddleware:
    def __init__(self, app, *, config: AuthConfig) -> None:
        self.app = app
        self.config = config

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        import asyncio

        from starlette.requests import Request

        request = Request(scope, receive=receive)
        policy = policy_for(request.method, request.url.path)
        try:
            await asyncio.wait_for(self.app(scope, receive, send), timeout=policy.timeout_seconds)
        except asyncio.TimeoutError:
            _metric_inc("monsoongrid_request_timeouts_total", route=_template(request.url.path))
            response = auth_error_response("REQUEST_TIMEOUT", "request exceeded the route timeout",
                                           status=504, request_id=_request_id(request), retryable=True)
            await response(scope, receive, send)


# --------------------------------------------------------------------------- #
# FastAPI dependency form (§5.5) — for the documented api.py follow-up patch
# --------------------------------------------------------------------------- #


def current_principal(request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise AuthRequired("missing")
    return principal


def require_roles(*roles: str) -> Callable[..., Principal]:
    required = frozenset(roles)

    def dependency(request) -> Principal:
        principal = current_principal(request)
        if not (principal.roles & required):
            raise AuthzDenied(required_role="|".join(sorted(required)))
        return principal

    return dependency

