from uuid import UUID
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from app.core.config import get_settings
from app.core.rate_limit import limiter
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import AuditLog, Tenant, TenantMembership
from app.schemas.common import AuditLogOut, MembershipCreate, MembershipInvite, MembershipOut, MembershipUpdate, TenantCreate, TenantOut
from app.security.auth import Actor, get_user_id, require_permission, require_second_factor
from app.services.audit import record
from app.services.brevo import invitation_html, send_brevo_email
from app.services.supabase_admin import create_user

router = APIRouter(prefix="/tenants", tags=["tenants"])
VALID_ROLES = {"owner", "admin", "sales", "purchases", "inventory", "finance", "viewer"}


def require_owner_role_change(actor: Actor, role_code: str) -> None:
    if role_code == "owner" and actor.role_code != "owner":
        raise HTTPException(status_code=403, detail="Solo un propietario puede asignar el rol de propietario")


def ensure_active_owner_remains(db: Session, membership: TenantMembership, changes: MembershipUpdate) -> None:
    loses_owner = membership.is_active and membership.role_code == "owner" and (
        changes.is_active is False or (changes.role_code is not None and changes.role_code != "owner")
    )
    if loses_owner:
        owners = db.scalar(select(func.count(TenantMembership.id)).where(
            TenantMembership.tenant_id == membership.tenant_id,
            TenantMembership.role_code == "owner",
            TenantMembership.is_active.is_(True),
        )) or 0
        if owners <= 1:
            raise HTTPException(status_code=422, detail="La empresa debe conservar al menos un propietario activo")


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
def add_membership(request: Request, response: Response, data: MembershipCreate, actor: Actor = Depends(require_permission("tenants:manage")), _: Actor = Depends(require_second_factor), db: Session = Depends(get_db)):
    if data.role_code not in VALID_ROLES:
        raise HTTPException(status_code=422, detail="Rol no válido")
    require_owner_role_change(actor, data.role_code)
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
async def invite_member(request: Request, response: Response, data: MembershipInvite, actor: Actor = Depends(require_permission("tenants:manage")), _: Actor = Depends(require_second_factor), db: Session = Depends(get_db)):
    if data.role_code not in VALID_ROLES:
        raise HTTPException(status_code=422, detail="Rol no válido")
    require_owner_role_change(actor, data.role_code)
    settings = get_settings()
    if not settings.supabase_service_role_key:
        raise HTTPException(status_code=503, detail="El servicio de invitaciones no está configurado")
    # Credentials are created server-side and travel only once to the exact
    # email entered by the administrator. Existing global accounts are not
    # overwritten, avoiding a cross-company administrator resetting a password.
    user_id = await create_user(settings, email=str(data.email), temporary_password=data.temporary_password)
    membership = db.scalar(select(TenantMembership).where(TenantMembership.tenant_id == actor.tenant_id, TenantMembership.user_id == user_id))
    if membership:
        membership.role_code = data.role_code
        membership.is_active = True
        raise HTTPException(status_code=409, detail="La cuenta ya tiene acceso a esta empresa; no se reemplazó su contraseña.")
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
    await send_brevo_email(
        settings,
        recipient=str(data.email),
        subject="Tus credenciales temporales de EnterpriseCloud",
        html=invitation_html(email=str(data.email), temporary_password=data.temporary_password, tenant_name=db.get(Tenant, actor.tenant_id).name, role=data.role_code),
    )
    return membership


@router.patch("/memberships/{membership_id}", response_model=MembershipOut)
@limiter.limit("20/minute")
def update_membership(request: Request, response: Response, membership_id: UUID, data: MembershipUpdate, actor: Actor = Depends(require_permission("tenants:manage")), _: Actor = Depends(require_second_factor), db: Session = Depends(get_db)):
    membership = db.scalar(select(TenantMembership).where(TenantMembership.id == membership_id, TenantMembership.tenant_id == actor.tenant_id))
    if not membership:
        raise HTTPException(status_code=404, detail="Membresía no encontrada")
    if data.role_code is not None:
        if data.role_code not in VALID_ROLES:
            raise HTTPException(status_code=422, detail="Rol no válido")
        require_owner_role_change(actor, data.role_code)
    if membership.role_code == "owner" and actor.role_code != "owner":
        raise HTTPException(status_code=403, detail="Solo un propietario puede modificar otro propietario")
    ensure_active_owner_remains(db, membership, data)
    if data.role_code is not None:
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
