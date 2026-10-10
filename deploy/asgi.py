"""Composition root (design §8.1).

Wraps the product app WITHOUT modifying api.py: imports it, mounts the ops and
scheduler routers, and installs the middleware stack in the fixed §8.1 order.

Config is resolved at module scope ON PURPOSE: an AuthConfigError here is an
uncaught exception during uvicorn's import of this module, so the process exits
non-zero without ever listening and Cloud Run's startup probe never passes. This
module is therefore NEVER unit-imported; the unit/integration tests construct
AuthConfig explicitly and mount the same middleware on a test-local FastAPI app.
"""

from __future__ import annotations

import logging
from pathlib import Path

from monsoon_grid import auth, observability, tenancy  # noqa: F401
from monsoon_grid.api import app as product_app

from deploy import cadence
from deploy.source_revision import resolve_source_revision

_ROOT = Path(__file__).resolve().parent.parent
SOURCE_REVISION, SOURCE_TREE_DIGEST = resolve_source_revision(_ROOT)

config = auth.AuthConfig.from_env()  # raises AuthConfigError -> process exits

observability.install_logging(gcp_project_id=config.gcp_project_id, revision=SOURCE_REVISION)
registry = observability.install_metrics()
audit = observability.AuditLog()
rate_store = auth.InMemoryTokenBucketStore()

# build_info gauge
_build_info = registry.get("monsoongrid_build_info")
if _build_info is not None:
    import sys

    try:
        import scipy

        scipy_version = scipy.__version__
    except Exception:
        scipy_version = "unavailable"
    _build_info.set(  # type: ignore[attr-defined]
        1,
        revision=SOURCE_REVISION,
        source_tree_digest=SOURCE_TREE_DIGEST[:16],
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        scipy_version=scipy_version,
    )

# Routers first (no change to existing routes).
product_app.include_router(observability.ops_router, prefix="/_ops")
product_app.include_router(cadence.sched_router, prefix="/_sched")

# Middleware: add_middleware applies in reverse, so the outermost-to-innermost
# execution order ends up exactly §8.1 (1 RequestContext ... 7 SecurityHeaders).
product_app.add_middleware(auth.SecurityHeadersMiddleware, config=config)
product_app.add_middleware(auth.RateLimitMiddleware, config=config, store=rate_store, stage="principal")
product_app.add_middleware(auth.AuthMiddleware, config=config, audit=audit)
product_app.add_middleware(auth.RateLimitMiddleware, config=config, store=rate_store, stage="ip")
product_app.add_middleware(auth.BodyLimitMiddleware, config=config)
product_app.add_middleware(auth.TimeoutMiddleware, config=config)
product_app.add_middleware(observability.RequestContextMiddleware, registry=registry)


@product_app.exception_handler(Exception)
async def _unhandled(request, exc):  # pragma: no cover - exercised by container run
    logging.getLogger("monsoon_grid.api").exception("internal_error")
    return auth.auth_error_response(
        "INTERNAL_ERROR", "an internal error occurred", status=500,
        request_id=getattr(request.state, "request_id", "0" * 32),
    )


# Prefetch JWKS in production so an instance that cannot reach Google's keys does
# not become ready.
if config.mode == "production":
    try:
        role_map = auth.load_role_map(config)
        _jwks = auth.JwksCache(config=config)
        _jwks.prefetch_or_raise()
    except Exception:  # the readiness probe will report trust/keys unavailable
        logging.getLogger("monsoon_grid.api").warning("jwks_prefetch_failed")

app = product_app
