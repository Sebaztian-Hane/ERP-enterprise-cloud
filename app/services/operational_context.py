"""Resolve the legal operational context of a transaction safely.

The tenant remains the isolation boundary.  This helper makes the warehouse →
branch → legal entity chain explicit and refuses an incomplete configuration
once the tenant has begun using the legal-entity master data.
"""

from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Branch, LegalEntity, Warehouse


@dataclass(frozen=True)
class OperationalContext:
    warehouse: Warehouse
    branch: Branch | None
    legal_entity: LegalEntity | None


def resolve_warehouse_context(db: Session, *, tenant_id: UUID, warehouse_id: UUID) -> OperationalContext:
    """Return a tenant-safe context derived solely from an active warehouse.

    Legacy tenants without legal entities can keep posting transactions while
    they complete their setup.  As soon as active entity/branch configuration
    exists, a warehouse must be assigned to a valid active branch.  That keeps
    documents from silently falling back to an ambiguous issuer.
    """
    warehouse = db.scalar(select(Warehouse).where(
        Warehouse.id == warehouse_id,
        Warehouse.tenant_id == tenant_id,
        Warehouse.is_active.is_(True),
    ))
    if not warehouse:
        raise HTTPException(status_code=404, detail="El almacén no existe o no pertenece a la empresa activa")

    if warehouse.branch_id is None:
        configured_legal_entity = db.scalar(select(LegalEntity.id).where(
            LegalEntity.tenant_id == tenant_id,
            LegalEntity.is_active.is_(True),
        ).limit(1))
        if configured_legal_entity:
            raise HTTPException(
                status_code=422,
                detail="Asigna el almacén a una sucursal activa antes de registrar documentos operativos",
            )
        return OperationalContext(warehouse=warehouse, branch=None, legal_entity=None)

    branch = db.scalar(select(Branch).where(
        Branch.id == warehouse.branch_id,
        Branch.tenant_id == tenant_id,
        Branch.is_active.is_(True),
    ))
    if not branch:
        raise HTTPException(status_code=409, detail="El almacén está vinculado a una sucursal inválida o inactiva")
    legal_entity = db.scalar(select(LegalEntity).where(
        LegalEntity.id == branch.legal_entity_id,
        LegalEntity.tenant_id == tenant_id,
        LegalEntity.is_active.is_(True),
    ))
    if not legal_entity:
        raise HTTPException(status_code=409, detail="La sucursal del almacén no tiene una entidad legal activa")
    return OperationalContext(warehouse=warehouse, branch=branch, legal_entity=legal_entity)
