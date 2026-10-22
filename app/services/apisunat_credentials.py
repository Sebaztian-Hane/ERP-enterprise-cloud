"""Tenant and legal-entity scoped APISUNAT credential handling."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import EntityAPISunatCredential


@dataclass(frozen=True)
class APISunatCredentials:
    id: UUID | None
    legal_entity_id: UUID
    issuer_ruc: str
    persona_id: str
    persona_token: str
    invoice_series: str
    receipt_series: str
    source: str = "entity"


def _fernet() -> Fernet:
    key = get_settings().credential_encryption_key
    if not key:
        raise HTTPException(status_code=503, detail="El cifrado de credenciales fiscales no está configurado en el servidor")
    try:
        return Fernet(key.encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="La clave de cifrado fiscal del servidor no es válida") from exc


def encrypt_persona_token(token: str) -> str:
    return _fernet().encrypt(token.encode("utf-8")).decode("utf-8")


def decrypt_persona_token(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise HTTPException(status_code=503, detail="No se pudo descifrar la configuración fiscal de esta empresa") from exc


def resolve_apisunat_credentials(
    db: Session, *, tenant_id: UUID, legal_entity_id: UUID, issuer_ruc: str,
) -> APISunatCredentials:
    credential = db.scalar(select(EntityAPISunatCredential).where(
        EntityAPISunatCredential.tenant_id == tenant_id,
        EntityAPISunatCredential.legal_entity_id == legal_entity_id,
        EntityAPISunatCredential.is_active.is_(True),
    ))
    if credential:
        if credential.issuer_ruc != issuer_ruc:
            raise HTTPException(status_code=409, detail="La configuración APISUNAT no coincide con el RUC de la entidad legal")
        return APISunatCredentials(
            id=credential.id, legal_entity_id=legal_entity_id, issuer_ruc=credential.issuer_ruc,
            persona_id=credential.persona_id, persona_token=decrypt_persona_token(credential.persona_token_ciphertext),
            invoice_series=credential.invoice_series, receipt_series=credential.receipt_series,
        )

    # Transitional compatibility only: it preserves the already configured
    # first issuer while the provider registers it in /oreo. It can never be
    # used to issue for another RUC/tenant and is not used for new tenants.
    settings = get_settings()
    if (
        issuer_ruc == (settings.apisunat_issuer_ruc or "").strip()
        and settings.apisunat_persona_id and settings.apisunat_persona_token
    ):
        return APISunatCredentials(
            id=None, legal_entity_id=legal_entity_id, issuer_ruc=issuer_ruc,
            persona_id=settings.apisunat_persona_id, persona_token=settings.apisunat_persona_token,
            invoice_series=settings.apisunat_invoice_series, receipt_series=settings.apisunat_receipt_series,
            source="legacy",
        )
    raise HTTPException(
        status_code=422,
        detail="Esta entidad legal no tiene credenciales APISUNAT configuradas. Regístralas en la consola del proveedor antes de emitir.",
    )
