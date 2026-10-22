"""Resolve payment terms server-side and retain immutable posting context."""

from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class PaymentTermContext:
    id: UUID
    code: str
    name: str
    days_due: int
    due_date: date


def resolve_payment_term(
    db: Session,
    *,
    tenant_id: UUID,
    payment_term_id: UUID | None,
    supplied_due_date: date | None,
    document_date: date,
) -> tuple[PaymentTermContext | None, date]:
    """Return the approved tenant-owned term and its deterministic due date.

    A caller may preserve a manual due date only when no payment term has been
    selected. Allowing both would create an unauditable conflict in the UI and
    let clients silently override commercial policy.
    """
    if payment_term_id is None:
        return None, supplied_due_date or document_date

    row = db.execute(
        text(
            """
            select id, code, name, days_due
            from payment_terms
            where tenant_id=:tenant and id=:payment_term and is_active=true
            """
        ),
        {"tenant": str(tenant_id), "payment_term": str(payment_term_id)},
    ).mappings().first()
    if not row:
        raise HTTPException(
            status_code=422,
            detail="La condición de pago no está activa o no pertenece a la empresa activa",
        )

    due_date = document_date + timedelta(days=int(row["days_due"]))
    if supplied_due_date is not None and supplied_due_date != due_date:
        raise HTTPException(
            status_code=422,
            detail="El vencimiento debe derivarse de la condición de pago seleccionada",
        )
    return (
        PaymentTermContext(
            id=row["id"],
            code=row["code"],
            name=row["name"],
            days_due=int(row["days_due"]),
            due_date=due_date,
        ),
        due_date,
    )
