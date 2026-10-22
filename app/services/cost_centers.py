"""Tenant-scoped cost-center resolution for operational documents."""

from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class CostCenterSnapshot:
    id: UUID
    code: str
    name: str


def resolve_cost_center(
    db: Session, *, tenant_id: UUID, cost_center_id: UUID | None,
) -> CostCenterSnapshot:
    """Return an active tenant-owned center, or the tenant's GENERAL center."""
    if cost_center_id:
        row = db.execute(text("""
            select id, code, name from cost_centers
            where tenant_id=:tenant and id=:cost_center and is_active=true
        """), {"tenant": str(tenant_id), "cost_center": str(cost_center_id)}).mappings().first()
        if not row:
            raise HTTPException(status_code=422, detail="El centro de costo no está activo o no pertenece a la empresa")
    else:
        row = db.execute(text("""
            select id, code, name from cost_centers
            where tenant_id=:tenant and code='GENERAL' and is_active=true
            order by id limit 1
        """), {"tenant": str(tenant_id)}).mappings().first()
        if not row:
            raise HTTPException(status_code=422, detail="Configura un centro de costo GENERAL activo antes de registrar la operación")
    return CostCenterSnapshot(id=row["id"], code=row["code"], name=row["name"])
