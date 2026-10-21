"""Provider-only cross-tenant administration.

This router intentionally does not accept X-Tenant-ID. Access is granted only
after a verified Supabase JWT is matched against PLATFORM_ADMIN_EMAILS on the
server. The /oreo front-end path is merely an entry point, never authorization.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.rate_limit import limiter
from app.db.session import get_db
from app.models.entities import Tenant, TenantMembership
from app.schemas.common import PlatformTenantOut, PlatformTenantUpdate
from app.security.auth import PlatformActor, require_platform_admin
from app.services.audit import record


router = APIRouter(prefix="/platform", tags=["platform"])


def tenant_payload(tenant: Tenant, active_members: int) -> PlatformTenantOut:
    return PlatformTenantOut(
        id=tenant.id,
        name=tenant.name,
        timezone=tenant.timezone,
        is_active=tenant.is_active,
        created_at=tenant.created_at,
        active_members=active_members,
    )


@router.get("/tenants", response_model=list[PlatformTenantOut])
@limiter.limit("60/minute")
def list_platform_tenants(
    request: Request,
    response: Response,
    _: PlatformActor = Depends(require_platform_admin),
    db: Session = Depends(get_db),
):
    member_count = func.count(TenantMembership.id).filter(TenantMembership.is_active.is_(True)).label("active_members")
    rows = db.execute(
        select(Tenant, member_count)
        .outerjoin(TenantMembership, TenantMembership.tenant_id == Tenant.id)
        .group_by(Tenant.id)
        .order_by(Tenant.created_at.desc())
    ).all()
    return [tenant_payload(tenant, int(count)) for tenant, count in rows]


@router.patch("/tenants/{tenant_id}", response_model=PlatformTenantOut)
@limiter.limit("20/minute")
def update_platform_tenant(
    request: Request,
    response: Response,
    tenant_id: UUID,
    data: PlatformTenantUpdate,
    actor: PlatformActor = Depends(require_platform_admin),
    db: Session = Depends(get_db),
):
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    tenant.is_active = data.is_active
    record(
        db,
        tenant_id=tenant.id,
        actor_user_id=actor.user_id,
        action="platform_activate_tenant" if data.is_active else "platform_suspend_tenant",
        module="platform",
        entity="tenant",
        entity_id=tenant.id,
    )
    db.commit()
    db.refresh(tenant)
    active_members = db.scalar(
        select(func.count(TenantMembership.id)).where(
            TenantMembership.tenant_id == tenant.id,
            TenantMembership.is_active.is_(True),
        )
    ) or 0
    return tenant_payload(tenant, int(active_members))
