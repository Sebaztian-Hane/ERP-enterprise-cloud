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
from app.models.entities import TenantMembership


class Actor(BaseModel):
    user_id: UUID
    tenant_id: UUID
    role_code: str


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


ROLE_PERMISSIONS = {
    "owner": {"*"}, "admin": {"*"}, "sales": {"customers:read", "customers:write", "products:read", "sales:read", "sales:write", "documents:read", "documents:write"},
    "purchases": {"suppliers:read", "suppliers:write", "products:read", "purchases:read", "purchases:write"},
    "finance": {"payments:read", "payments:write", "accounts:read", "sales:read", "purchases:read", "reports:read", "documents:read", "documents:write", "treasury:read", "treasury:write"},
    "inventory": {"products:read", "products:write", "inventory:read", "inventory:write"},
    "viewer": {"customers:read", "products:read", "suppliers:read", "inventory:read", "sales:read", "purchases:read", "payments:read", "accounts:read", "reports:read", "documents:read"},
}


def require_permission(permission: str):
    def check(actor: Actor = Depends(get_actor)) -> Actor:
        granted = ROLE_PERMISSIONS.get(actor.role_code, set())
        if "*" not in granted and permission not in granted:
            raise HTTPException(status_code=403, detail="No tiene permiso para esta operación")
        return actor
    return check
