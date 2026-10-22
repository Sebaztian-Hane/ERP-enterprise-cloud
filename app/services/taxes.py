"""Tenant-scoped, effective-dated tax resolution for commercial documents."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class TaxRateSnapshot:
    id: UUID
    code: str
    name: str
    rate: Decimal


def resolve_default_tax_rate(db: Session, *, tenant_id: UUID, document_date: date) -> TaxRateSnapshot:
    row = db.execute(text("""
        select id, code, name, rate
        from tax_rates
        where tenant_id=:tenant
          and is_active=true
          and is_default=true
          and valid_from <= :document_date
          and (valid_to is null or valid_to >= :document_date)
        order by valid_from desc, code
        limit 1
    """), {"tenant": str(tenant_id), "document_date": document_date}).mappings().first()
    if row is None:
        raise HTTPException(
            status_code=422,
            detail="No existe una tasa tributaria predeterminada vigente para la fecha del documento",
        )
    return TaxRateSnapshot(id=row["id"], code=row["code"], name=row["name"], rate=Decimal(str(row["rate"])))
