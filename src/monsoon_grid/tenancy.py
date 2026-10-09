"""Tenant isolation primitives (design §6).

Answers "which data may this principal touch". Tenant id is derived from the
verified principal only, never from request content. For the single-tenant pilot
(ASM-1) every principal resolves to DEFAULT_TENANT_ID; the mechanism is real and
tested so the Postgres migration can carry a NOT NULL tenant column from day one.

Fail-closed: require_same_tenant raises on a mismatch AND on a None resource
tenant (an untagged resource is foreign, not public). The middleware surfaces the
denial as 404, not 403, so a mismatch does not confirm the resource exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Mapping

from .errors import MonsoonGridError

if TYPE_CHECKING:  # avoid an import cycle at runtime
    from .auth import AuthConfig, RoleMap

TENANT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
DEFAULT_TENANT_ID = "t-pilot"


class TenantScopeDenied(MonsoonGridError):
    code = "TENANT_SCOPE_DENIED"


@dataclass(frozen=True)
class TenantScope:
    tenant_id: str
    source: Literal["token_claim", "role_map", "single_tenant_default"]


def resolve_tenant(*, claims: Mapping[str, Any], role_map: "RoleMap", config: "AuthConfig") -> TenantScope:
    """Resolution order: token claim (multi-tenant future) -> role-map entry for
    the subject -> single-tenant default. In production with require_tenant_claim
    a missing/malformed claim raises rather than falling back."""
    if config.require_tenant_claim:
        claim = claims.get("tenant")
        if not isinstance(claim, str) or not TENANT_ID_RE.match(claim):
            raise TenantScopeDenied("tenant claim is required and must be well formed in this configuration")
        return TenantScope(tenant_id=claim, source="token_claim")

    # Optional claim honoured when present and valid even outside require mode.
    claim = claims.get("tenant")
    if isinstance(claim, str) and TENANT_ID_RE.match(claim):
        return TenantScope(tenant_id=claim, source="token_claim")

    subject = str(claims.get("sub", ""))
    mapped = getattr(role_map, "tenant_by_subject", {}) or {}
    tenant = mapped.get(subject)
    if isinstance(tenant, str) and TENANT_ID_RE.match(tenant):
        return TenantScope(tenant_id=tenant, source="role_map")

    return TenantScope(tenant_id=DEFAULT_TENANT_ID, source="single_tenant_default")


def require_same_tenant(scope: TenantScope, resource_tenant_id: str | None) -> None:
    """Raise TenantScopeDenied on a mismatch OR a None resource tenant."""
    if resource_tenant_id is None or resource_tenant_id != scope.tenant_id:
        raise TenantScopeDenied("resource is not within the caller's tenant scope")


def scoped_filter(scope: TenantScope) -> dict[str, str]:
    """The tenant predicate every adapter query must carry (§6, §13)."""
    return {"tenant_id": scope.tenant_id}


def tenant_prefixed_object_key(scope: TenantScope, digest: str) -> str:
    """Content-addressed object key namespaced by tenant: <tenant>/sha256/<digest>."""
    return f"{scope.tenant_id}/sha256/{digest}"
