"""Concurrency-safe operational folio allocation.

The sequence is advanced inside the caller's transaction, therefore an
idempotent retry reuses the stored response and a failed business transaction
does not consume a number. This service deliberately does not allocate CPE
correlatives: APISUNAT remains the authority for those fiscal values.
"""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.entities import DocumentSequence


DEFAULT_PREFIXES = {
    ("sales", "sale"): "V",
    ("purchases", "purchase"): "C",
}


def format_document_number(prefix: str | None, next_number: int) -> str:
    safe_prefix = (prefix or "DOC").strip().upper()
    return f"{safe_prefix}-{next_number:08d}"


def allocate_document_number(
    db: Session,
    *,
    tenant_id: UUID,
    module: str,
    document_type: str,
    legal_entity_id: UUID | None,
    branch_id: UUID | None,
) -> str:
    if (legal_entity_id is None) != (branch_id is None):
        raise ValueError("La entidad legal y la sucursal deben definirse juntas")

    scoped = legal_entity_id is not None
    conflict_where = (
        (DocumentSequence.legal_entity_id.is_not(None)) & (DocumentSequence.branch_id.is_not(None))
        if scoped
        else (DocumentSequence.legal_entity_id.is_(None)) & (DocumentSequence.branch_id.is_(None))
    )
    values = {
        "tenant_id": tenant_id,
        "legal_entity_id": legal_entity_id,
        "branch_id": branch_id,
        "module": module,
        "document_type": document_type,
        "prefix": DEFAULT_PREFIXES.get((module, document_type), "DOC"),
        "next_number": 1,
        "is_active": True,
    }
    db.execute(insert(DocumentSequence).values(**values).on_conflict_do_nothing(
        index_elements=[
            DocumentSequence.tenant_id,
            DocumentSequence.legal_entity_id,
            DocumentSequence.branch_id,
            DocumentSequence.module,
            DocumentSequence.document_type,
        ],
        index_where=conflict_where,
    ))
    sequence = db.scalar(select(DocumentSequence).where(
        DocumentSequence.tenant_id == tenant_id,
        DocumentSequence.legal_entity_id == legal_entity_id,
        DocumentSequence.branch_id == branch_id,
        DocumentSequence.module == module,
        DocumentSequence.document_type == document_type,
    ).with_for_update())
    if not sequence:
        raise HTTPException(status_code=409, detail="No se pudo reservar la numeración del documento")
    if not sequence.is_active:
        raise HTTPException(status_code=409, detail="La numeración de este documento está desactivada")
    number = format_document_number(sequence.prefix, sequence.next_number)
    sequence.next_number += 1
    return number
