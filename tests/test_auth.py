"""Auth, tenancy, and request-integrity tests (design §5, §6, §8; AC-1..19)."""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric import padding as _padding
from cryptography.hazmat.primitives.hashes import SHA256
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from monsoon_grid import auth, observability as obs, tenancy


# --------------------------------------------------------------------------- #
# JWT / JWKS test harness
# --------------------------------------------------------------------------- #


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class _Signer:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "test-kid-1"

    def jwks(self) -> dict:
        numbers = self.key.public_key().public_numbers()
        n = numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
        e = numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")
        return {"keys": [{"kty": "RSA", "alg": "RS256", "kid": self.kid, "n": _b64u(n), "e": _b64u(e)}]}

    def token(self, *, header: dict | None = None, payload: dict | None = None, tamper: bool = False, sign: bool = True) -> str:
        hdr = header or {"alg": "RS256", "kid": self.kid, "typ": "JWT"}
        now = int(time.time())
        pl = payload or {
            "iss": "https://accounts.google.com",
            "aud": "https://verify.local",
            "sub": "1029384756",
            "exp": now + 600,
            "iat": now,
            "email": "ops@example.com",
            "email_verified": True,
        }
        hb = _b64u(json.dumps(hdr).encode())
        pb = _b64u(json.dumps(pl).encode())
        signing_input = f"{hb}.{pb}".encode("ascii")
        if sign and hdr.get("alg") == "RS256":
            sig = self.key.sign(signing_input, _padding.PKCS1v15(), SHA256())
        else:
            sig = b"notasignature"
        token = f"{hb}.{pb}.{_b64u(sig)}"
        if tamper:
            parts = token.split(".")
            raw = bytearray(base64.urlsafe_b64decode(auth._pad(parts[1])))
            raw[0] ^= 0x01
            parts[1] = _b64u(bytes(raw))
            token = ".".join(parts)
        return token


def _jwks_transport(signer: _Signer, counter: list[int]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        counter[0] += 1
        return httpx.Response(200, json=signer.jwks(), headers={"ETag": "v1"})

    return httpx.MockTransport(handler)


def _prod_config(**overrides) -> auth.AuthConfig:
    env = {
        "MONSOON_GRID_ENV": "production",
        "MONSOON_GRID_AUTH_MODE": "production",
        "MONSOON_GRID_AUTH_AUDIENCE": "https://verify.local",
        "MONSOON_GRID_ROLE_MAP": json.dumps({"schema_version": "1.0", "by_subject": {"1029384756": ["operator"]}}),
    }
    env.update(overrides)
    return auth.AuthConfig.from_env(env)


# --------------------------------------------------------------------------- #
# AC-1 / AC-2 fail-closed config
# --------------------------------------------------------------------------- #


def test_production_requires_audience_rolemap_mode():
    base = {"MONSOON_GRID_ENV": "production", "MONSOON_GRID_AUTH_MODE": "production",
            "MONSOON_GRID_AUTH_AUDIENCE": "a", "MONSOON_GRID_ROLE_MAP": '{"schema_version":"1.0"}'}
    for missing in ("MONSOON_GRID_AUTH_AUDIENCE", "MONSOON_GRID_ROLE_MAP"):
        env = dict(base)
        env[missing] = ""
        with pytest.raises(auth.AuthConfigError):
            auth.AuthConfig.from_env(env)


def test_local_dev_forbidden_in_production():
    with pytest.raises(auth.AuthConfigError):
        auth.AuthConfig.from_env({"MONSOON_GRID_ENV": "production", "MONSOON_GRID_AUTH_MODE": "local_dev",
                                  "MONSOON_GRID_AUTH_AUDIENCE": "a", "MONSOON_GRID_ROLE_MAP": "{}"})


def test_dev_token_forbidden_in_production():
    with pytest.raises(auth.AuthConfigError):
        auth.AuthConfig.from_env({"MONSOON_GRID_ENV": "production", "MONSOON_GRID_AUTH_MODE": "production",
                                  "MONSOON_GRID_AUTH_AUDIENCE": "a", "MONSOON_GRID_ROLE_MAP": "{}",
                                  "MONSOON_GRID_DEV_BEARER_TOKEN": "x"})


# --------------------------------------------------------------------------- #
# AC-3 seven token rejection cases + success
# --------------------------------------------------------------------------- #


def _verifier(signer: _Signer, config: auth.AuthConfig, counter: list[int], role_map=None):
    jwks = auth.JwksCache(config=config, transport=_jwks_transport(signer, counter))
    rm = role_map or auth.RoleMap(by_subject={"1029384756": ["operator"]})
    return auth.GoogleOidcVerifier(config=config, jwks=jwks, role_map=rm)


def test_valid_token_verifies_and_seven_rejections():
    obs.install_metrics(obs.MetricsRegistry())
    signer = _Signer()
    config = _prod_config()
    counter = [0]
    verifier = _verifier(signer, config, counter)
    now = datetime.now(timezone.utc)

    principal = verifier.verify(f"Bearer {signer.token()}", now=now)
    assert principal.subject == "1029384756"
    assert principal.actor_id == "oidc:1029384756"
    assert "operator" in principal.roles

    cases = [
        signer.token(tamper=True),  # a) altered payload
        signer.token(payload={"iss": "https://accounts.google.com", "aud": "wrong", "sub": "1029384756", "exp": int(time.time()) + 600}),  # b
        signer.token(payload={"iss": "https://evil", "aud": "https://verify.local", "sub": "1029384756", "exp": int(time.time()) + 600}),  # c
        signer.token(payload={"iss": "https://accounts.google.com", "aud": "https://verify.local", "sub": "1029384756", "exp": int(time.time()) - 3600}),  # d
        signer.token(header={"alg": "none", "kid": signer.kid}, sign=False),  # e
        signer.token(header={"alg": "HS256", "kid": signer.kid}, sign=False),  # f
        signer.token(header={"alg": "RS256"}),  # g missing kid
    ]
    for tok in cases:
        with pytest.raises(auth.AuthTokenInvalid):
            verifier.verify(f"Bearer {tok}", now=now)


# --------------------------------------------------------------------------- #
# AC-4 JWKS cache fetch count
# --------------------------------------------------------------------------- #


def test_jwks_cache_fetch_count():
    obs.install_metrics(obs.MetricsRegistry())
    signer = _Signer()
    config = _prod_config()
    counter = [0]
    clock_val = [datetime.now(timezone.utc)]
    jwks = auth.JwksCache(config=config, transport=_jwks_transport(signer, counter), clock=lambda: clock_val[0])
    for _ in range(5):
        assert jwks.get(signer.kid) is not None
    assert counter[0] == 1  # one fetch inside the TTL
    clock_val[0] = clock_val[0] + timedelta(seconds=config.jwks_ttl_seconds + 1)
    assert jwks.get(signer.kid) is not None
    assert counter[0] == 2  # exactly one more after TTL


# --------------------------------------------------------------------------- #
# RoleMap validate_for and tenancy
# --------------------------------------------------------------------------- #


def test_rolemap_rejects_operator_approver_overlap():
    rm = auth.RoleMap(by_subject={"x": ["operator", "approver"]})
    with pytest.raises(auth.AuthConfigError):
        rm.validate_for("production")


def test_rolemap_default_roles_empty_in_production():
    rm = auth.RoleMap(default_roles=["viewer"])
    with pytest.raises(auth.AuthConfigError):
        rm.validate_for("production")


def test_tenancy_resolution_and_guard():
    config = _prod_config()
    rm = auth.RoleMap(by_subject={"1029384756": ["operator"]})
    scope = tenancy.resolve_tenant(claims={"sub": "1029384756"}, role_map=rm, config=config)
    assert scope.tenant_id == tenancy.DEFAULT_TENANT_ID
    tenancy.require_same_tenant(scope, "t-pilot")
    with pytest.raises(tenancy.TenantScopeDenied):
        tenancy.require_same_tenant(scope, "t-beta")
    with pytest.raises(tenancy.TenantScopeDenied):
        tenancy.require_same_tenant(scope, None)


# --------------------------------------------------------------------------- #
# AC-8..11 approval integrity
# --------------------------------------------------------------------------- #


def _principal(subject="2222", roles=("approver",), actor=None):
    return auth.Principal(
        actor_id=actor or f"oidc:{subject}", subject=subject, issuer="https://accounts.google.com",
        auth_method="google_oidc", roles=frozenset(roles), tenant_id="t-pilot",
        token_fingerprint="f" * 64, expires_at=None, email=None, email_verified=False,
    )


def test_authorize_approval_actor_from_principal_not_body():
    now = datetime.now(timezone.utc)
    author = auth.DecisionAuthor(subject="1111", author_class="human", recorded_at=now)
    result = auth.authorize_approval(
        principal=_principal("2222"), replay_id="r", decision_id="r:decision:1",
        expected_state_digest="a" * 64, observed_state_digest="a" * 64,
        idempotency_key="idem-key-123", action="approve", reason="approve it",
        author=author, now=now,
    )
    assert isinstance(result, auth.ApprovalAuthorization)
    assert result.actor_id == "oidc:2222"
    import re
    assert re.fullmatch(r"^[A-Za-z0-9_.:-]+$", result.actor_id)


def test_authorize_approval_rejections():
    now = datetime.now(timezone.utc)
    author = auth.DecisionAuthor(subject="1111", author_class="human", recorded_at=now)
    common = dict(replay_id="r", decision_id="d", action="approve", reason="ok", author=author, now=now)

    stale = auth.authorize_approval(principal=_principal(), expected_state_digest="a" * 64,
                                    observed_state_digest="b" * 64, idempotency_key="idem-key-123", **common)
    assert isinstance(stale, auth.ApprovalRejected) and stale.code == "APPROVAL_STATE_STALE"

    bad_idem = auth.authorize_approval(principal=_principal(), expected_state_digest="a" * 64,
                                       observed_state_digest="a" * 64, idempotency_key="xy", **common)
    assert isinstance(bad_idem, auth.ApprovalRejected) and bad_idem.code == "APPROVAL_IDEMPOTENCY_REQUIRED"

    role_denied = auth.authorize_approval(principal=_principal(roles=("operator",)), expected_state_digest="a" * 64,
                                          observed_state_digest="a" * 64, idempotency_key="idem-key-123", **common)
    assert isinstance(role_denied, auth.ApprovalRejected) and role_denied.code == "APPROVAL_ROLE_DENIED"


def test_authorize_approval_self_and_unknown():
    now = datetime.now(timezone.utc)
    self_author = auth.DecisionAuthor(subject="2222", author_class="human", recorded_at=now)
    rej = auth.authorize_approval(principal=_principal("2222"), replay_id="r", decision_id="d",
                                  expected_state_digest="a" * 64, observed_state_digest="a" * 64,
                                  idempotency_key="idem-key-123", action="approve", reason="ok",
                                  author=self_author, now=now)
    assert isinstance(rej, auth.ApprovalRejected) and rej.code == "APPROVAL_SELF_APPROVAL"

    unknown = auth.authorize_approval(principal=_principal("2222"), replay_id="r", decision_id="d",
                                      expected_state_digest="a" * 64, observed_state_digest="a" * 64,
                                      idempotency_key="idem-key-123", action="approve", reason="ok",
                                      author=None, now=now, production=True)
    assert isinstance(unknown, auth.ApprovalRejected) and unknown.code == "APPROVAL_AUTHOR_UNKNOWN"


def test_binding_digest_changes_on_each_field():
    now = datetime.now(timezone.utc)
    author = auth.DecisionAuthor(subject="1111", author_class="human", recorded_at=now)
    base = dict(principal=_principal("2222"), replay_id="r", decision_id="d",
                expected_state_digest="a" * 64, observed_state_digest="a" * 64,
                idempotency_key="idem-key-123", action="approve", reason="ok", author=author, now=now)
    original = auth.authorize_approval(**base).approval_binding_digest

    variants = [
        {**base, "replay_id": "r2"},
        {**base, "decision_id": "d2"},
        {**base, "expected_state_digest": "c" * 64, "observed_state_digest": "c" * 64},
        {**base, "principal": _principal("3333")},  # actor + subject
        {**base, "action": "reject"},
        {**base, "idempotency_key": "idem-key-999"},
    ]
    for variant in variants:
        res = auth.authorize_approval(**variant)
        assert res.approval_binding_digest != original

    # issuer / auth_method via a differently constructed principal
    alt = auth.Principal(actor_id="oidc:2222", subject="2222", issuer="accounts.google.com",
                         auth_method="google_oidc", roles=frozenset({"approver"}), tenant_id="t-pilot",
                         token_fingerprint="f" * 64, expires_at=None, email=None, email_verified=False)
    assert auth.authorize_approval(**{**base, "principal": alt}).approval_binding_digest != original


# --------------------------------------------------------------------------- #
# AC-10b decision author index advance-route trap
# --------------------------------------------------------------------------- #


def test_decision_author_index_records_and_ttl():
    now = datetime.now(timezone.utc)
    index = auth.DecisionAuthorIndex(clock=lambda: now)
    index.record_author(replay_id="replay-A", decision_id="replay-A:decision:42", subject="oidc:1", author_class="human")
    author = index.author_of(replay_id="replay-A", decision_id="replay-A:decision:42")
    assert author is not None and author.subject == "oidc:1"
    # a different decision id (the post-increment slot) is NOT the same author key
    assert index.author_of(replay_id="replay-A", decision_id="replay-A:decision:43") is None


# --------------------------------------------------------------------------- #
# §5.7 token bucket arithmetic
# --------------------------------------------------------------------------- #


def test_token_bucket_refill():
    now = [0.0]
    store = auth.InMemoryTokenBucketStore(clock=lambda: now[0])
    spec = auth.BucketSpec(capacity=3, refill_per_second=1.0, scope="principal")
    for _ in range(3):
        assert store.consume("k", spec, now=now[0]).allowed
    assert not store.consume("k", spec, now=now[0]).allowed
    now[0] = 1.0
    assert store.consume("k", spec, now=now[0]).allowed


# --------------------------------------------------------------------------- #
# is_https predicate
# --------------------------------------------------------------------------- #


class _FakeReq:
    def __init__(self, headers, scheme):
        self.headers = headers
        self.url = type("U", (), {"scheme": scheme})()


def test_is_https_predicate():
    assert auth.is_https(_FakeReq({"x-forwarded-proto": "https"}, "http")) is True
    assert auth.is_https(_FakeReq({}, "http")) is False
    assert auth.is_https(_FakeReq({}, "https")) is True


# --------------------------------------------------------------------------- #
# AC-19 error envelope shape
# --------------------------------------------------------------------------- #


def test_auth_error_response_envelope():
    resp = auth.auth_error_response("AUTH_TOKEN_INVALID", "the Authorization token is invalid",
                                    status=401, request_id="r" * 32)
    body = json.loads(bytes(resp.body))
    assert body["schema_version"] == "1.0"
    assert body["request_id"] == "r" * 32
    assert body["status"] == "error"
    assert body["data"] is None
    err = body["error"]
    for key in ("code", "message", "field_errors", "replay_id", "slot_index", "recoverable", "retryable"):
        assert key in err
    assert "secrettoken" not in body["error"]["message"]


# --------------------------------------------------------------------------- #
# Integration app (§8.1) — AC-5, 6, 7, 7a, 10a, 12, 13, 13a, 15, 16, 17, 17a
# --------------------------------------------------------------------------- #


def _build_app(signer, config, counter, role_map, store=None, audit=None):
    app = FastAPI()
    rm = role_map

    @app.get("/portfolio")
    def read_portfolio():
        return {"ok": True}

    @app.post("/planning")
    def planning():
        return {"ok": True}

    @app.post("/approvals")
    def approvals():
        return {"ok": True}

    @app.post("/demo")
    def demo():
        return {"ok": True}

    @app.post("/api/deliberate")
    def deliberate():
        return {"entered": True}

    jwks = auth.JwksCache(config=config, transport=_jwks_transport(signer, counter))
    verifier = auth.GoogleOidcVerifier(config=config, jwks=jwks, role_map=rm)
    rate_store = store or auth.InMemoryTokenBucketStore()

    app.add_middleware(auth.SecurityHeadersMiddleware, config=config)
    app.add_middleware(auth.RateLimitMiddleware, config=config, store=rate_store, stage="principal")
    app.add_middleware(auth.AuthMiddleware, config=config, verifier=verifier, role_map=rm, audit=audit)
    app.add_middleware(auth.RateLimitMiddleware, config=config, store=rate_store, stage="ip")
    app.add_middleware(auth.BodyLimitMiddleware, config=config)
    return app


def _dev_rolemap():
    return auth.RoleMap(by_subject={"1029384756": ["operator"], "2": ["approver"], "3": ["viewer"], "4": ["auditor"], "5": ["admin"]}, allow_role_overlap=True)


def _bearer(signer, sub="1029384756", roles_sub=None):
    return {"Authorization": f"Bearer {signer.token(payload=_payload(sub))}"}


def _payload(sub):
    now = int(time.time())
    return {"iss": "https://accounts.google.com", "aud": "https://verify.local", "sub": sub,
            "exp": now + 600, "iat": now, "email": f"{sub}@example.com", "email_verified": True}


def test_integration_unauthenticated_mutation_rejected():
    obs.install_metrics(obs.MetricsRegistry())
    signer = _Signer()
    config = _prod_config()
    client = TestClient(_build_app(signer, config, [0], _dev_rolemap()))

    r = client.post("/planning", json={})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "AUTH_REQUIRED"

    r = client.post("/planning", headers={"Authorization": "Bearer not.a.jwt"}, json={})
    assert r.status_code == 401
    assert r.json()["error"]["code"] in {"AUTH_TOKEN_MALFORMED", "AUTH_TOKEN_INVALID"}
    assert "not.a.jwt" not in r.text


def test_integration_role_matrix():
    obs.install_metrics(obs.MetricsRegistry())
    signer = _Signer()
    config = _prod_config()
    client = TestClient(_build_app(signer, config, [0], _dev_rolemap()))

    # viewer can read, cannot mutate
    assert client.get("/portfolio", headers=_bearer(signer, "3")).status_code == 200
    assert client.post("/planning", headers=_bearer(signer, "3"), json={}).status_code == 403

    # auditor denied on all mutating including approvals
    assert client.post("/approvals", headers=_bearer(signer, "4"), json={}).status_code == 403

    # operator can plan, cannot approve
    assert client.post("/planning", headers=_bearer(signer, "1029384756"), json={}).status_code == 200
    assert client.post("/approvals", headers=_bearer(signer, "1029384756"), json={}).status_code == 403

    # approver can approve, cannot plan
    assert client.post("/approvals", headers=_bearer(signer, "2"), json={}).status_code == 200
    assert client.post("/planning", headers=_bearer(signer, "2"), json={}).status_code == 403

    # admin denied on /demo and /api/deliberate (MEDIUM-4), allowed on reads
    assert client.post("/demo", headers=_bearer(signer, "5"), json={}).status_code == 403
    assert client.post("/api/deliberate", headers=_bearer(signer, "5"), json={}).status_code == 403
    assert client.get("/portfolio", headers=_bearer(signer, "5")).status_code == 200


def test_integration_rate_limit_principal():
    obs.install_metrics(obs.MetricsRegistry())
    signer = _Signer()
    config = _prod_config()
    client = TestClient(_build_app(signer, config, [0], _dev_rolemap()))
    headers = _bearer(signer, "1029384756")
    statuses = [client.post("/demo", headers=headers, json={}).status_code for _ in range(5)]
    assert 429 in statuses  # mutating_heavy capacity is 3 per principal
    # a read still works
    assert client.get("/portfolio", headers=headers).status_code == 200


def test_integration_security_headers():
    obs.install_metrics(obs.MetricsRegistry())
    signer = _Signer()
    config = _prod_config()
    client = TestClient(_build_app(signer, config, [0], _dev_rolemap()))
    r = client.get("/portfolio", headers={**_bearer(signer, "3"), "X-Forwarded-Proto": "https"})
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"

    r2 = client.get("/portfolio", headers=_bearer(signer, "3"))
    assert "strict-transport-security" not in r2.headers

    https_client = TestClient(_build_app(signer, config, [0], _dev_rolemap()), base_url="https://testserver")
    r3 = https_client.get("/portfolio", headers=_bearer(signer, "3"))
    assert r3.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"


def test_integration_body_limit():
    obs.install_metrics(obs.MetricsRegistry())
    signer = _Signer()
    config = _prod_config()
    client = TestClient(_build_app(signer, config, [0], _dev_rolemap()))
    # /demo body limit is 8 KiB; send > 8 KiB
    big = {"blob": "x" * (9 * 1024)}
    r = client.post("/demo", headers=_bearer(signer, "1029384756"), json=big)
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_integration_no_idempotency_header_check(monkeypatch):
    obs.install_metrics(obs.MetricsRegistry())
    signer = _Signer()
    config = _prod_config()
    # the middleware adds NO idempotency header check: a mutating request with valid
    # credentials and no Idempotency-Key reaches the route handler (AC-17a).
    client = TestClient(_build_app(signer, config, [0], _dev_rolemap()))
    r = client.post("/api/deliberate", headers=_bearer(signer, "1029384756"), json={})
    assert r.status_code == 200
    assert r.json()["entered"] is True


def test_source_grep_no_idempotency_header():
    import pathlib

    text = pathlib.Path(auth.__file__).read_text(encoding="utf-8")
    # AC-17a: no Idempotency-Key handling in the middleware stack. Any reference
    # must be confined to a CORS allow_headers list.
    for line in text.splitlines():
        if "Idempotency-Key" in line:
            assert "allow_headers" in line


# --------------------------------------------------------------------------- #
# AC-7b route-coverage check pinned against the real route set
# --------------------------------------------------------------------------- #


def test_route_coverage_matches_expected_nine():
    from deploy.check_route_coverage import EXPECTED_PATCH_ROUTES, analyze

    result = analyze()
    assert result["routes_unmatched"] == []
    assert sorted(result["routes_missing_require_roles"]) == sorted(EXPECTED_PATCH_ROUTES)


# --------------------------------------------------------------------------- #
# AC-40a cadence slot-key derivation + fast-loop skip
# --------------------------------------------------------------------------- #


def test_cadence_slot_key_derivation():
    from deploy.cadence import _IST, derive_slot_key

    assert derive_slot_key(datetime(2026, 7, 1, 6, 7, tzinfo=_IST)) == "sched-20260701-24"
    assert derive_slot_key(datetime(2026, 7, 1, 6, 14, 59, tzinfo=_IST)) == "sched-20260701-24"
    assert derive_slot_key(datetime(2026, 7, 1, 6, 15, 0, tzinfo=_IST)) == "sched-20260701-25"
    assert derive_slot_key(datetime(2026, 7, 1, 0, 0, tzinfo=_IST)) == "sched-20260701-00"
    assert derive_slot_key(datetime(2026, 7, 1, 23, 59, tzinfo=_IST)) == "sched-20260701-95"
    import re

    for key in ("sched-20260701-24", "sched-20260701-95"):
        assert re.fullmatch(r"^[A-Za-z0-9_.:-]+$", key)


def test_cadence_fast_loop_skips():
    obs.install_metrics(obs.MetricsRegistry())
    from deploy.cadence import _IST, run_fast_loop

    now = datetime(2026, 7, 1, 6, 7, tzinfo=_IST)
    status, body = run_fast_loop(now=now, replays={})
    assert status == 409 and body["code"] == "NO_ACTIVE_REPLAY"

    class _Rec:
        slot_index = 10

    status, body = run_fast_loop(now=now, replays={"a": _Rec(), "b": _Rec()})
    assert status == 409 and body["code"] == "AMBIGUOUS_ACTIVE_REPLAY"
    skips = obs.default_registry().get("monsoongrid_cadence_skips_total")
    assert sum(skips._values.values()) == 2


# --------------------------------------------------------------------------- #
# AC-40 source revision resolution across branches
# --------------------------------------------------------------------------- #


def test_source_revision_excludes_dependencies_but_binds_built_ui(tmp_path):
    from deploy.source_revision import source_tree_digest

    ui = tmp_path / "src" / "monsoon_grid" / "ui"
    dependencies = ui / "node_modules" / "dependency"
    dependencies.mkdir(parents=True)
    artifact = ui / "dist" / "assets" / "app.js"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("application-v1", encoding="utf-8")
    before = source_tree_digest(tmp_path)
    (dependencies / "index.js").write_text("not-shipped", encoding="utf-8")
    assert source_tree_digest(tmp_path) == before
    artifact.write_text("application-v2", encoding="utf-8")
    assert source_tree_digest(tmp_path) != before


def test_source_revision_branches(tmp_path, monkeypatch):
    from deploy import source_revision

    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    monkeypatch.setenv("SOURCE_REVISION", "explicit-rev")
    rev, digest = source_revision.resolve_source_revision(tmp_path)
    assert rev == "explicit-rev" and len(digest) == 64

    monkeypatch.delenv("SOURCE_REVISION", raising=False)
    monkeypatch.setenv("COMMIT_SHA", "0123456789abcdef0000")
    rev, _ = source_revision.resolve_source_revision(tmp_path)
    assert rev == "sha-0123456789ab"

    monkeypatch.delenv("COMMIT_SHA", raising=False)
    # no git repo under tmp_path -> tree digest fallback
    rev, digest = source_revision.resolve_source_revision(tmp_path)
    assert rev == f"tree-{digest[:12]}"
