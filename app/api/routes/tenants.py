from uuid import UUID
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from app.core.config import get_settings
from app.core.rate_limit import limiter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import AuditLog, Tenant, TenantMembership
from app.schemas.common import AuditLogOut, MembershipCreate, MembershipInvite, MembershipOut, MembershipUpdate, TenantCreate, TenantOut
from app.security.auth import Actor, get_user_id, require_permission
from app.services.audit import record

router = APIRouter(prefix="/tenants", tags=["tenants"])
VALID_ROLES = {"owner", "admin", "sales", "purchases", "inventory", "finance", "viewer"}


@router.get("/my", response_model=list[TenantOut])
def my_tenants(user_id: UUID = Depends(get_user_id), db: Session = Depends(get_db)):
    return db.scalars(select(Tenant).join(TenantMembership).where(TenantMembership.user_id == user_id, TenantMembership.is_active.is_(True), Tenant.is_active.is_(True)).order_by(Tenant.name)).all()


@router.post("", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/hour")
def create_tenant(request: Request, response: Response, data: TenantCreate, user_id: UUID = Depends(get_user_id), db: Session = Depends(get_db)):
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
def add_membership(request: Request, response: Response, data: MembershipCreate, actor: Actor = Depends(require_permission("tenants:manage")), db: Session = Depends(get_db)):
    if data.role_code not in VALID_ROLES:
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


@router.post("/invitations", response_model=MembershipOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def invite_member(request: Request, response: Response, data: MembershipInvite, actor: Actor = Depends(require_permission("tenants:manage")), db: Session = Depends(get_db)):
    if data.role_code not in VALID_ROLES:
        raise HTTPException(status_code=422, detail="Rol no válido")
    settings = get_settings()
    if not settings.supabase_service_role_key:
        raise HTTPException(status_code=503, detail="El servicio de invitaciones no está configurado")
    body = {"email": str(data.email)}
    if settings.invite_redirect_url:
        body["redirect_to"] = settings.invite_redirect_url
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{str(settings.supabase_url).rstrip('/')}/auth/v1/invite",
                headers={"Authorization": f"Bearer {settings.supabase_service_role_key}", "apikey": settings.supabase_service_role_key},
                json=body,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="No fue posible contactar el servicio de invitaciones") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=422, detail="No fue posible enviar la invitación; verifica que el correo no tenga ya una cuenta activa")
    try:
        payload = response.json()
        user_id = UUID(payload.get("user", payload)["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="El servicio de invitaciones devolvió una respuesta no válida") from exc
    membership = db.scalar(select(TenantMembership).where(TenantMembership.tenant_id == actor.tenant_id, TenantMembership.user_id == user_id))
    if membership:
        membership.role_code = data.role_code
        membership.is_active = True
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="resend_invite", module="tenants", entity="tenant_membership", entity_id=membership.id)
        db.commit()
        db.refresh(membership)
        return membership
    membership = TenantMembership(tenant_id=actor.tenant_id, user_id=user_id, role_code=data.role_code)
    db.add(membership)
    db.flush()
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="invite_member", module="tenants", entity="tenant_membership", entity_id=membership.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El usuario ya tiene acceso a esta empresa") from exc
    db.refresh(membership)
    return membership


@router.patch("/memberships/{membership_id}", response_model=MembershipOut)
@limiter.limit("20/minute")
def update_membership(request: Request, response: Response, membership_id: UUID, data: MembershipUpdate, actor: Actor = Depends(require_permission("tenants:manage")), db: Session = Depends(get_db)):
    membership = db.scalar(select(TenantMembership).where(TenantMembership.id == membership_id, TenantMembership.tenant_id == actor.tenant_id))
    if not membership:
        raise HTTPException(status_code=404, detail="Membresía no encontrada")
    if data.role_code is not None:
        if data.role_code not in VALID_ROLES:
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
