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
from app.models.entities import EntityAPISunatCredential, LegalEntity, MembershipModuleSetting, Tenant, TenantMembership, TenantModuleSetting
from app.schemas.common import APISunatCredentialStatus, APISunatCredentialUpsert, ModuleToggle, PlatformTenantCreate, PlatformTenantOut, PlatformTenantUpdate
from app.security.auth import PlatformActor, require_platform_admin
from app.services.audit import record
from app.services.supabase_admin import create_user
from app.services.apisunat_credentials import encrypt_persona_token


router = APIRouter(prefix="/platform", tags=["platform"])
MODULES = [
    ("sales", "Ventas"), ("customers", "Clientes"), ("quotes", "Cotizaciones"),
    ("sales_orders", "Pedidos de venta"), ("purchases", "Compras"),
    ("suppliers", "Proveedores"), ("purchase_orders", "Órdenes de compra"),
    ("inventory", "Inventario"), ("products", "Productos"), ("finance", "Finanzas"),
    ("reconciliation", "Conciliación"), ("documents", "Documentos"), ("reports", "Reportes"),
    ("operations", "Control operativo"), ("administration", "Administración"),
]
MODULE_CODES = {code for code, _ in MODULES}


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


@router.post("/tenants", response_model=PlatformTenantOut, status_code=201)
@limiter.limit("10/hour")
async def create_platform_tenant(request: Request, response: Response, data: PlatformTenantCreate, actor: PlatformActor = Depends(require_platform_admin), db: Session = Depends(get_db)):
    from app.core.config import get_settings
    # Validate server-side encryption before provisioning an external auth user.
    # The plaintext is intentionally discarded immediately after ciphertext is made.
    persona_token_ciphertext = encrypt_persona_token(data.apisunat_persona_token)
    user_id = await create_user(get_settings(), email=str(data.owner_email), temporary_password=data.temporary_password)
    tenant = Tenant(name=data.name.strip(), timezone=data.timezone)
    db.add(tenant); db.flush()
    legal_entity = LegalEntity(
        tenant_id=tenant.id, code="PRINCIPAL", legal_name=data.name.strip(),
        trade_name=data.name.strip(), tax_id=data.issuer_ruc, fiscal_address=data.fiscal_address.strip(),
        is_default=True, is_active=True,
    )
    db.add(legal_entity); db.flush()
    db.add(EntityAPISunatCredential(
        tenant_id=tenant.id, legal_entity_id=legal_entity.id, issuer_ruc=data.issuer_ruc,
        persona_id=data.apisunat_persona_id.strip(), persona_token_ciphertext=persona_token_ciphertext,
        invoice_series=data.apisunat_invoice_series, receipt_series=data.apisunat_receipt_series,
    ))
    db.add(TenantMembership(tenant_id=tenant.id, user_id=user_id, role_code="owner"))
    record(db, tenant_id=tenant.id, actor_user_id=actor.user_id, action="platform_create_tenant", module="platform", entity="tenant", entity_id=tenant.id)
    db.commit(); db.refresh(tenant)
    return tenant_payload(tenant, 1)


@router.get("/tenants/{tenant_id}/apisunat", response_model=list[APISunatCredentialStatus])
def get_tenant_apisunat_credentials(
    tenant_id: UUID,
    _: PlatformActor = Depends(require_platform_admin),
    db: Session = Depends(get_db),
):
    if not db.get(Tenant, tenant_id):
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    credentials = {
        row.legal_entity_id: row
        for row in db.scalars(select(EntityAPISunatCredential).where(
            EntityAPISunatCredential.tenant_id == tenant_id,
        ))
    }
    entities = db.scalars(select(LegalEntity).where(
        LegalEntity.tenant_id == tenant_id,
    ).order_by(LegalEntity.code)).all()
    return [
        APISunatCredentialStatus(
            legal_entity_id=entity.id,
            issuer_ruc=entity.tax_id,
            invoice_series=credentials[entity.id].invoice_series if entity.id in credentials else "F001",
            receipt_series=credentials[entity.id].receipt_series if entity.id in credentials else "B001",
            is_configured=entity.id in credentials and credentials[entity.id].is_active,
            source="entity" if entity.id in credentials else "missing",
        )
        for entity in entities
    ]


@router.put("/tenants/{tenant_id}/apisunat", response_model=APISunatCredentialStatus)
@limiter.limit("20/hour")
def upsert_tenant_apisunat_credential(
    request: Request,
    response: Response,
    tenant_id: UUID,
    data: APISunatCredentialUpsert,
    actor: PlatformActor = Depends(require_platform_admin),
    db: Session = Depends(get_db),
):
    entity = db.scalar(select(LegalEntity).where(
        LegalEntity.id == data.legal_entity_id,
        LegalEntity.tenant_id == tenant_id,
        LegalEntity.is_active.is_(True),
    ))
    if not entity:
        raise HTTPException(status_code=404, detail="Entidad legal no encontrada en esta empresa")
    ciphertext = encrypt_persona_token(data.persona_token)
    credential = db.scalar(select(EntityAPISunatCredential).where(
        EntityAPISunatCredential.tenant_id == tenant_id,
        EntityAPISunatCredential.legal_entity_id == entity.id,
    ))
    if credential:
        credential.persona_id = data.persona_id.strip()
        credential.persona_token_ciphertext = ciphertext
        credential.issuer_ruc = entity.tax_id
        credential.invoice_series = data.invoice_series
        credential.receipt_series = data.receipt_series
        credential.is_active = True
    else:
        credential = EntityAPISunatCredential(
            tenant_id=tenant_id, legal_entity_id=entity.id, issuer_ruc=entity.tax_id,
            persona_id=data.persona_id.strip(), persona_token_ciphertext=ciphertext,
            invoice_series=data.invoice_series, receipt_series=data.receipt_series,
        )
        db.add(credential)
    record(
        db, tenant_id=tenant_id, actor_user_id=actor.user_id,
        action="platform_configure_apisunat", module="platform", entity="legal_entity",
        entity_id=entity.id, detail=f"RUC {entity.tax_id}; token actualizado",
    )
    db.commit()
    return APISunatCredentialStatus(
        legal_entity_id=entity.id, issuer_ruc=entity.tax_id,
        invoice_series=credential.invoice_series, receipt_series=credential.receipt_series,
        is_configured=True, source="entity",
    )


@router.get("/tenants/{tenant_id}/module-access")
def get_module_access(tenant_id: UUID, _: PlatformActor = Depends(require_platform_admin), db: Session = Depends(get_db)):
    if not db.get(Tenant, tenant_id):
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    tenant_settings = {row.module_code: row.is_enabled for row in db.scalars(select(TenantModuleSetting).where(TenantModuleSetting.tenant_id == tenant_id))}
    members = db.scalars(select(TenantMembership).where(TenantMembership.tenant_id == tenant_id).order_by(TenantMembership.created_at)).all()
    overrides = db.scalars(select(MembershipModuleSetting).where(MembershipModuleSetting.tenant_id == tenant_id)).all()
    by_member: dict[str, dict[str, bool]] = {}
    for setting in overrides:
        by_member.setdefault(str(setting.membership_id), {})[setting.module_code] = setting.is_enabled
    return {
        "modules": [{"code": code, "label": label, "is_enabled": tenant_settings.get(code, True)} for code, label in MODULES],
        "members": [{"id": str(member.id), "user_id": str(member.user_id), "role_code": member.role_code, "is_active": member.is_active, "overrides": by_member.get(str(member.id), {})} for member in members],
    }


@router.patch("/tenants/{tenant_id}/modules")
def update_tenant_module(tenant_id: UUID, data: ModuleToggle, actor: PlatformActor = Depends(require_platform_admin), db: Session = Depends(get_db)):
    if data.module_code not in MODULE_CODES:
        raise HTTPException(status_code=422, detail="Módulo no válido")
    if not db.get(Tenant, tenant_id):
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    setting = db.get(TenantModuleSetting, {"tenant_id": tenant_id, "module_code": data.module_code})
    if setting: setting.is_enabled = data.is_enabled
    else: db.add(TenantModuleSetting(tenant_id=tenant_id, module_code=data.module_code, is_enabled=data.is_enabled))
    record(db, tenant_id=tenant_id, actor_user_id=actor.user_id, action="platform_module_toggle", module="platform", entity="tenant_module", detail=f"{data.module_code}={data.is_enabled}")
    db.commit(); return {"module_code": data.module_code, "is_enabled": data.is_enabled}


@router.patch("/tenants/{tenant_id}/members/{membership_id}/modules")
def update_member_module(tenant_id: UUID, membership_id: UUID, data: ModuleToggle, actor: PlatformActor = Depends(require_platform_admin), db: Session = Depends(get_db)):
    if data.module_code not in MODULE_CODES:
        raise HTTPException(status_code=422, detail="Módulo no válido")
    membership = db.scalar(select(TenantMembership).where(TenantMembership.id == membership_id, TenantMembership.tenant_id == tenant_id))
    if not membership:
        raise HTTPException(status_code=404, detail="Usuario no encontrado en esta empresa")
    setting = db.scalar(select(MembershipModuleSetting).where(MembershipModuleSetting.membership_id == membership_id, MembershipModuleSetting.module_code == data.module_code))
    if setting: setting.is_enabled = data.is_enabled
    else: db.add(MembershipModuleSetting(tenant_id=tenant_id, membership_id=membership_id, module_code=data.module_code, is_enabled=data.is_enabled))
    record(db, tenant_id=tenant_id, actor_user_id=actor.user_id, action="platform_member_module_toggle", module="platform", entity="membership_module", entity_id=membership.id, detail=f"{data.module_code}={data.is_enabled}")
    db.commit(); return {"module_code": data.module_code, "is_enabled": data.is_enabled}
