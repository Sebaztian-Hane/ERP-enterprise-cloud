from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, status
from app.core.rate_limit import limiter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import AuditLog, Tenant, TenantMembership
from app.schemas.common import AuditLogOut, MembershipCreate, MembershipOut, MembershipUpdate, TenantCreate, TenantOut
from app.security.auth import Actor, get_user_id, require_permission
from app.services.audit import record

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.get("/my", response_model=list[TenantOut])
def my_tenants(user_id: UUID = Depends(get_user_id), db: Session = Depends(get_db)):
    return db.scalars(select(Tenant).join(TenantMembership).where(TenantMembership.user_id == user_id, TenantMembership.is_active.is_(True), Tenant.is_active.is_(True)).order_by(Tenant.name)).all()


@router.post("", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/hour")
def create_tenant(request: Request, data: TenantCreate, user_id: UUID = Depends(get_user_id), db: Session = Depends(get_db)):
    tenant = Tenant(name=data.name, timezone=data.timezone)
    db.add(tenant)
    db.flush()
    db.add(TenantMembership(tenant_id=tenant.id, user_id=user_id, role_code="owner"))
    record(db, tenant_id=tenant.id, actor_user_id=user_id, action="create", module="tenants", entity="tenant", entity_id=tenant.id)
    db.commit()
    db.refresh(tenant)
    return tenant


@router.get("/memberships", response_model=list[MembershipOut])
def list_memberships(actor: Actor = Depends(require_permission("tenants:manage")), db: Session = Depends(get_db)):
    return db.scalars(select(TenantMembership).where(TenantMembership.tenant_id == actor.tenant_id).order_by(TenantMembership.created_at)).all()


@router.post("/memberships", response_model=MembershipOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("20/minute")
def add_membership(request: Request, data: MembershipCreate, actor: Actor = Depends(require_permission("tenants:manage")), db: Session = Depends(get_db)):
    if data.role_code not in {"owner", "admin", "sales", "purchases", "inventory", "finance", "viewer"}:
        raise HTTPException(status_code=422, detail="Rol no válido")
    membership = TenantMembership(tenant_id=actor.tenant_id, user_id=data.user_id, role_code=data.role_code)
    db.add(membership)
    db.flush()
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="add_member", module="tenants", entity="tenant_membership", entity_id=membership.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El usuario ya tiene una membresía en esta empresa") from exc
    db.refresh(membership)
    return membership


@router.patch("/memberships/{membership_id}", response_model=MembershipOut)
@limiter.limit("20/minute")
def update_membership(request: Request, membership_id: UUID, data: MembershipUpdate, actor: Actor = Depends(require_permission("tenants:manage")), db: Session = Depends(get_db)):
    membership = db.scalar(select(TenantMembership).where(TenantMembership.id == membership_id, TenantMembership.tenant_id == actor.tenant_id))
    if not membership:
        raise HTTPException(status_code=404, detail="Membresía no encontrada")
    if data.role_code is not None:
        if data.role_code not in {"owner", "admin", "sales", "purchases", "inventory", "finance", "viewer"}:
            raise HTTPException(status_code=422, detail="Rol no válido")
        membership.role_code = data.role_code
    if data.is_active is not None:
        if membership.user_id == actor.user_id and not data.is_active:
            raise HTTPException(status_code=422, detail="No puede desactivar su propio acceso")
        membership.is_active = data.is_active
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="update_member", module="tenants", entity="tenant_membership", entity_id=membership.id)
    db.commit()
    db.refresh(membership)
    return membership


@router.get("/audit-logs", response_model=list[AuditLogOut])
def list_audit_logs(limit: int = 50, actor: Actor = Depends(require_permission("tenants:manage")), db: Session = Depends(get_db)):
    safe_limit = min(max(limit, 1), 200)
    return db.scalars(select(AuditLog).where(AuditLog.tenant_id == actor.tenant_id).order_by(AuditLog.created_at.desc()).limit(safe_limit)).all()
