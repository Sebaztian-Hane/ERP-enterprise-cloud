from fastapi import APIRouter, Depends, Request
from app.core.rate_limit import limiter
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import Tenant, TenantMembership
from app.schemas.common import TenantOut
from app.security.auth import Actor, get_actor, get_user_id

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me")
@limiter.limit("60/minute")
def me(request: Request, actor: Actor = Depends(get_actor)) -> Actor:
    return actor


@router.get("/tenants", response_model=list[TenantOut])
def available_tenants(user_id = Depends(get_user_id), db: Session = Depends(get_db)):
    return db.scalars(select(Tenant).join(TenantMembership).where(
        TenantMembership.user_id == user_id, TenantMembership.is_active.is_(True), Tenant.is_active.is_(True)
    ).order_by(Tenant.name)).all()
