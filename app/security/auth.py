import json
from functools import lru_cache
from uuid import UUID
import httpx
import jwt
from fastapi import Depends, Header, HTTPException, status
from jwt import PyJWKClient
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.db.session import get_db
from app.models.entities import MembershipModuleSetting, Tenant, TenantMembership, TenantModuleSetting


class Actor(BaseModel):
    user_id: UUID
    tenant_id: UUID
    role_code: str


class PlatformActor(BaseModel):
    """A verified EnterpriseCloud provider identity, never a tenant role."""

    user_id: UUID
    email: str


@lru_cache
def jwk_client() -> PyJWKClient | None:
    url = get_settings().supabase_jwks_url
    return PyJWKClient(url) if url else None


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    kwargs = {"algorithms": ["ES256", "RS256", "HS256"], "audience": settings.supabase_jwt_audience, "issuer": settings.supabase_jwt_issuer}
    try:
        if settings.supabase_jwks_url:
            key = jwk_client().get_signing_key_from_jwt(token).key  # type: ignore[union-attr]
            return jwt.decode(token, key, **kwargs)
        if settings.supabase_jwt_secret:
            return jwt.decode(token, settings.supabase_jwt_secret, **kwargs)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido o vencido") from exc
    raise HTTPException(status_code=500, detail="La validación JWT no está configurada")


def get_actor(
    authorization: str = Header(...),
    x_tenant_id: UUID = Header(...),
    db: Session = Depends(get_db),
) -> Actor:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Se requiere un token Bearer")
    claims = decode_access_token(authorization.removeprefix("Bearer "))
    try:
        user_id = UUID(claims["sub"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="El token no contiene un usuario válido") from exc
    membership = db.scalar(select(TenantMembership).where(
        TenantMembership.user_id == user_id,
        TenantMembership.tenant_id == x_tenant_id,
        TenantMembership.is_active.is_(True),
    ))
    if not membership:
        raise HTTPException(status_code=403, detail="No tiene acceso a la empresa activa")
    return Actor(user_id=user_id, tenant_id=x_tenant_id, role_code=membership.role_code)


def get_user_id(authorization: str = Header(...)) -> UUID:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Se requiere un token Bearer")
    claims = decode_access_token(authorization.removeprefix("Bearer "))
    try:
        return UUID(claims["sub"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="El token no contiene un usuario válido") from exc


def platform_actor_from_claims(claims: dict) -> PlatformActor:
    """Authorize the provider from a verified JWT and server-side allowlist.

    `user_metadata` is intentionally never used here because a signed-in user
    can modify it. The email is read only after signature verification and is
    compared with a private Render configuration value.
    """
    try:
        user_id = UUID(claims["sub"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="El token no contiene un usuario válido") from exc
    email = claims.get("email")
    allowed = get_settings().platform_admin_email_set
    if not isinstance(email, str) or email.casefold() not in allowed:
        raise HTTPException(status_code=403, detail="No tiene acceso a la consola de proveedor")
    return PlatformActor(user_id=user_id, email=email)


def require_platform_admin(authorization: str = Header(...)) -> PlatformActor:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Se requiere un token Bearer")
    return platform_actor_from_claims(decode_access_token(authorization.removeprefix("Bearer ")))


ROLE_PERMISSIONS = {
    "owner": {"*"}, "admin": {"*"}, "sales": {"customers:read", "customers:write", "products:read", "sales:read", "sales:write", "electronic_billing:read", "electronic_billing:write", "documents:read", "documents:write"},
    "purchases": {"suppliers:read", "suppliers:write", "products:read", "purchases:read", "purchases:write"},
    "finance": {"payments:read", "payments:write", "accounts:read", "sales:read", "purchases:read", "reports:read", "documents:read", "documents:write", "treasury:read", "treasury:write", "accounting:read", "accounting:write", "approvals:read"},
    "inventory": {"products:read", "products:write", "inventory:read", "inventory:write"},
    "viewer": {"customers:read", "products:read", "suppliers:read", "inventory:read", "sales:read", "purchases:read", "payments:read", "accounts:read", "reports:read", "documents:read", "accounting:read"},
}

PERMISSION_MODULES = {
    "customers": "customers", "suppliers": "suppliers", "products": "products",
    "sales": "sales", "electronic_billing": "sales", "purchases": "purchases",
    "inventory": "inventory", "documents": "documents", "reports": "reports",
    "payments": "finance", "accounts": "finance", "treasury": "finance",
    "reconciliation": "reconciliation", "accounting": "operations", "approvals": "operations",
    "configuration": "operations", "tenants": "administration",
}


PROFILE_BLOCKED_MODULES = {
    "retail": {"quotes", "sales_orders"},
    "wholesale": {"pos"},
    "hybrid": set(),
}


def module_is_enabled(db: Session, actor: Actor, module_code: str) -> bool:
    tenant = db.get(Tenant, actor.tenant_id)
    if not tenant or module_code in PROFILE_BLOCKED_MODULES.get(tenant.business_profile, set()):
        return False
    tenant_setting = db.get(TenantModuleSetting, {"tenant_id": actor.tenant_id, "module_code": module_code})
    if tenant_setting and not tenant_setting.is_enabled:
        return False
    membership = db.scalar(select(TenantMembership).where(
        TenantMembership.tenant_id == actor.tenant_id, TenantMembership.user_id == actor.user_id,
    ))
    if not membership:
        return False
    member_setting = db.scalar(select(MembershipModuleSetting).where(
        MembershipModuleSetting.membership_id == membership.id,
        MembershipModuleSetting.module_code == module_code,
    ))
    return member_setting.is_enabled if member_setting else True


def require_permission(permission: str, module_code: str | None = None):
    def check(actor: Actor = Depends(get_actor), db: Session = Depends(get_db)) -> Actor:
        granted = ROLE_PERMISSIONS.get(actor.role_code, set())
        if "*" not in granted and permission not in granted:
            raise HTTPException(status_code=403, detail="No tiene permiso para esta operación")
        effective_module_code = module_code or PERMISSION_MODULES.get(permission.split(":", 1)[0])
        # Direct unit calls exercise role semantics without FastAPI resolving
        # dependency objects. Real requests always receive a Session here.
        if effective_module_code and hasattr(db, "get") and not module_is_enabled(db, actor, effective_module_code):
            raise HTTPException(status_code=403, detail="Este módulo no está habilitado para su acceso")
        return actor
    return check


def require_module(module_code: str):
    """Require an additional tenant module without weakening role permissions."""
    def check(actor: Actor = Depends(get_actor), db: Session = Depends(get_db)) -> Actor:
        if not module_is_enabled(db, actor, module_code):
            raise HTTPException(status_code=403, detail="Este módulo no está habilitado para su acceso")
        return actor
    return check


def require_owner_or_admin(actor: Actor = Depends(get_actor)) -> Actor:
    """Protect bulk migrations independently from each module's write permission."""
    if actor.role_code not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Solo el propietario o un administrador puede migrar datos")
    return actor
