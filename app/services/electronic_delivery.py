"""Safe delivery of a pre-reserved electronic document through the outbox."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.entities import ElectronicDocument, LegalEntity
from app.services.apisunat import APISunatError, send_bill
from app.services.apisunat_credentials import resolve_apisunat_credentials
from app.services.audit import record


def provider_status(value: object) -> str:
    return {
        "PENDIENTE": "submitted",
        "ACEPTADO": "accepted",
        "RECHAZADO": "rejected",
        "EXCEPCION": "observed",
        "OBSERVADO": "observed",
    }.get(str(value).upper(), "unknown")


def deliver_cpe_submission(event: dict[str, object]) -> None:
    """Attempt exactly one provider submission for a durable reservation.

    An interrupted submission is intentionally marked ``unknown``. Retrying an
    opaque timeout could create a second CPE at the provider, so reconciliation
    is required before any human-initiated follow-up.
    """
    tenant_id = UUID(str(event["tenant_id"]))
    document_id = UUID(str(event["document_id"]))
    actor_user_id = UUID(str(event["actor_user_id"]))
    with SessionLocal() as db:
        document = db.scalar(
            select(ElectronicDocument)
            .where(ElectronicDocument.id == document_id, ElectronicDocument.tenant_id == tenant_id)
            .with_for_update()
        )
        if not document or document.status in {"accepted", "rejected", "observed", "unknown", "voided"}:
            return
        if document.status == "submitted":
            document.status = "unknown"
            document.last_error = "La entrega al proveedor fue interrumpida; requiere conciliación antes de reemitir"
            record(db, tenant_id=tenant_id, actor_user_id=actor_user_id, action="submission_unknown", module="electronic_billing", entity="electronic_document", entity_id=document.id, detail=document.file_name)
            db.commit()
            return
        submission = (document.provider_payload or {}).get("submission")
        if not isinstance(submission, dict) or not isinstance(submission.get("document_body"), dict):
            document.status = "unknown"
            document.last_error = "No se encontró el contenido reservado para enviar el comprobante"
            record(db, tenant_id=tenant_id, actor_user_id=actor_user_id, action="submission_unknown", module="electronic_billing", entity="electronic_document", entity_id=document.id, detail=document.file_name)
            db.commit()
            return
        file_name = document.file_name
        document_body = submission["document_body"]
        customer_email = submission.get("customer_email")
        if not document.legal_entity_id:
            document.status = "unknown"
            document.last_error = "El comprobante no tiene entidad legal; no puede enviarse al proveedor"
            db.commit()
            return
        legal_entity = db.scalar(select(LegalEntity).where(
            LegalEntity.id == document.legal_entity_id,
            LegalEntity.tenant_id == tenant_id,
        ))
        if not legal_entity:
            document.status = "unknown"
            document.last_error = "No se encontró la entidad legal del comprobante"
            db.commit()
            return
        credentials = resolve_apisunat_credentials(
            db, tenant_id=tenant_id, legal_entity_id=legal_entity.id, issuer_ruc=legal_entity.tax_id,
        )
        document.status = "submitted"
        db.commit()

    settings = get_settings()
    request_payload = {
        "personaId": credentials.persona_id,
        "personaToken": credentials.persona_token,
        "fileName": file_name,
        "documentBody": document_body,
    }
    if customer_email:
        request_payload["customerEmail"] = customer_email
    try:
        provider_response = send_bill(settings, request_payload)
        if not provider_response.get("documentId"):
            raise APISunatError("APISUNAT no devolvió el identificador del comprobante")
    except APISunatError as exc:
        with SessionLocal() as db:
            document = db.scalar(select(ElectronicDocument).where(ElectronicDocument.id == document_id, ElectronicDocument.tenant_id == tenant_id).with_for_update())
            if document:
                document.status = "unknown"
                document.last_error = str(exc)
                record(db, tenant_id=tenant_id, actor_user_id=actor_user_id, action="submission_unknown", module="electronic_billing", entity="electronic_document", entity_id=document.id, detail=document.file_name)
                db.commit()
        return

    with SessionLocal() as db:
        document = db.scalar(select(ElectronicDocument).where(ElectronicDocument.id == document_id, ElectronicDocument.tenant_id == tenant_id).with_for_update())
        if not document:
            return
        document.status = provider_status(provider_response.get("status"))
        document.provider_document_id = provider_response.get("documentId")
        document.provider_payload = provider_response
        document.xml_url = provider_response.get("xml") if isinstance(provider_response.get("xml"), str) else None
        document.cdr_url = provider_response.get("cdr") if isinstance(provider_response.get("cdr"), str) else None
        document.last_error = None if document.status in {"submitted", "accepted"} else str(provider_response.get("faults") or provider_response.get("notes") or "APISUNAT reportó observaciones")
        record(db, tenant_id=tenant_id, actor_user_id=actor_user_id, action="provider_response", module="electronic_billing", entity="electronic_document", entity_id=document.id, detail=f"{document.file_name} · {document.status}")
        db.commit()
