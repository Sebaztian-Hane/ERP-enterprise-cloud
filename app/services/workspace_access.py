"""Read the effective access without granting or changing a single role."""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import MembershipModuleSetting, Tenant, TenantMembership, TenantModuleSetting
from app.security.auth import Actor, PROFILE_BLOCKED_MODULES, ROLE_PERMISSIONS
from app.services.module_catalog import MODULES
from app.services.module_hierarchy import entitlement_enabled


def workspace_access(actor: Actor, db: Session) -> dict:
    tenant = db.get(Tenant, actor.tenant_id)
    member = db.scalar(select(TenantMembership).where(TenantMembership.tenant_id == actor.tenant_id, TenantMembership.user_id == actor.user_id, TenantMembership.is_active.is_(True)))
    if not tenant or not tenant.is_active or not member:
        raise HTTPException(status_code=403, detail="No tienes una membresía activa en esta empresa")
    company = {item.module_code: item.is_enabled for item in db.scalars(select(TenantModuleSetting).where(TenantModuleSetting.tenant_id == actor.tenant_id)).all()}
    personal = {item.module_code: item.is_enabled for item in db.scalars(select(MembershipModuleSetting).where(MembershipModuleSetting.membership_id == member.id, MembershipModuleSetting.tenant_id == actor.tenant_id)).all()}
    blocked = PROFILE_BLOCKED_MODULES.get(tenant.business_profile, set())
    return {
        "role_code": actor.role_code,
        "permissions": sorted(ROLE_PERMISSIONS.get(actor.role_code, set())),
        "modules": {code: entitlement_enabled(code, company, personal, blocked) for code, _ in MODULES},
    }
