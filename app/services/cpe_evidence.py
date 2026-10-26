"""Private, append-only evidence capture for electronic invoices.

The fiscal provider remains the authority for delivery status.  This module
copies evidence returned by that provider into private Storage and records a
hash for every capture so a later refresh never erases prior evidence.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urljoin, urlparse
from uuid import UUID, uuid4

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import ElectronicDocument, ElectronicDocumentArtifact, ElectronicDocumentEvent
from app.storage.supabase import upload_bytes


_REDACTED_KEYS = {"authorization", "password", "secret", "token", "personatoken", "apikey", "api_key"}
_ARTIFACT_FIELDS = {"xml": ("xml", "application/xml"), "cdr": ("cdr", "application/xml")}


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def redact_provider_data(value: Any) -> Any:
    """Avoid persisting accidental credentials returned or echoed by a provider."""
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if str(key).replace("-", "").replace("_", "").casefold() in _REDACTED_KEYS else redact_provider_data(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_provider_data(item) for item in value]
    return value


def record_cpe_event(
    db: Session,
    *,
    document: ElectronicDocument,
    event_type: str,
    actor_user_id: UUID | None,
    payload: object | None = None,
    detail: str | None = None,
) -> None:
    db.add(ElectronicDocumentEvent(
        tenant_id=document.tenant_id,
        electronic_document_id=document.id,
        event_type=event_type,
        document_status=document.status,
        provider_document_id=document.provider_document_id,
        payload_hash=content_hash(canonical_json_bytes(redact_provider_data(payload))) if payload is not None else None,
        detail=detail,
        actor_user_id=actor_user_id,
    ))


def _artifact_path(document: ElectronicDocument, artifact_type: str, extension: str) -> str:
    return f"tenants/{document.tenant_id}/cpe/{document.id}/{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{uuid4().hex}-{artifact_type}.{extension}"


def _allowed_provider_url(value: str) -> str:
    settings = get_settings()
    base_url = str(settings.apisunat_base_url).rstrip("/") + "/"
    parsed = urlparse(value)
    if not parsed.scheme and value.startswith("/"):
        value = urljoin(base_url, value)
        parsed = urlparse(value)
    allowed_hosts = {urlparse(base_url).hostname}
    allowed_hosts.update(host.strip().casefold() for host in settings.apisunat_artifact_allowed_hosts.split(",") if host.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.hostname.casefold() not in allowed_hosts:
        raise ValueError("La URL de evidencia no pertenece a un host fiscal autorizado")
    return value


def _download_provider_artifact(url: str) -> tuple[bytes, str]:
    settings = get_settings()
    approved_url = _allowed_provider_url(url)
    limit = settings.max_document_size_bytes
    try:
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            with client.stream("GET", approved_url) as response:
                response.raise_for_status()
                chunks: list[bytes] = []
                received = 0
                for chunk in response.iter_bytes():
                    received += len(chunk)
                    if received > limit:
                        raise ValueError("La evidencia excede el tamaño máximo permitido")
                    chunks.append(chunk)
                return b"".join(chunks), response.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
    except httpx.HTTPError as exc:
        raise ValueError("No se pudo descargar la evidencia desde el proveedor") from exc


def _append_artifact(
    db: Session,
    *,
    document: ElectronicDocument,
    artifact_type: str,
    status: str,
    actor_user_id: UUID | None,
    source_url: str | None = None,
    storage_path: str | None = None,
    content_type: str | None = None,
    content: bytes | None = None,
    error_message: str | None = None,
) -> None:
    db.add(ElectronicDocumentArtifact(
        tenant_id=document.tenant_id,
        electronic_document_id=document.id,
        artifact_type=artifact_type,
        capture_status=status,
        source_url=source_url,
        storage_path=storage_path,
        content_type=content_type,
        size_bytes=len(content) if content is not None else None,
        content_hash=content_hash(content) if content is not None else None,
        provider_document_id=document.provider_document_id,
        error_message=error_message,
        captured_by=actor_user_id,
    ))


def capture_provider_evidence(
    db: Session,
    *,
    document: ElectronicDocument,
    provider_response: dict[str, Any],
    event_type: str,
    actor_user_id: UUID | None,
) -> None:
    """Append the provider response and all available XML/CDR/PDF captures.

    A storage or download failure is itself a traceable event and must never
    roll back a provider outcome already accepted by the fiscal service.
    """
    safe_response = redact_provider_data(provider_response)
    response_bytes = canonical_json_bytes(safe_response)
    document.provider_payload = safe_response
    document.provider_response_hash = content_hash(response_bytes)
    document.provider_response_at = datetime.now(timezone.utc)
    record_cpe_event(
        db,
        document=document,
        event_type=event_type,
        actor_user_id=actor_user_id,
        payload=safe_response,
        detail=f"Respuesta fiscal preservada · {document.status}",
    )
    try:
        response_path = _artifact_path(document, "provider-response", "json")
        upload_bytes(response_path, response_bytes, content_type="application/json")
        _append_artifact(
            db, document=document, artifact_type="provider_response", status="stored", actor_user_id=actor_user_id,
            storage_path=response_path, content_type="application/json", content=response_bytes,
        )
    except Exception as exc:  # Evidence failure must remain visible but not erase the CPE result.
        _append_artifact(
            db, document=document, artifact_type="provider_response", status="failed", actor_user_id=actor_user_id,
            error_message=str(exc)[:500],
        )

    for response_key, (artifact_type, fallback_content_type) in _ARTIFACT_FIELDS.items():
        source_url = safe_response.get(response_key)
        if not isinstance(source_url, str) or not source_url.strip():
            _append_artifact(
                db, document=document, artifact_type=artifact_type, status="unavailable", actor_user_id=actor_user_id,
                error_message="El proveedor no devolvió este artefacto",
            )
            continue
        try:
            content, response_content_type = _download_provider_artifact(source_url)
            extension = "pdf" if artifact_type == "pdf" else "xml"
            path = _artifact_path(document, artifact_type, extension)
            mime = response_content_type if response_content_type not in {"", "application/octet-stream"} else fallback_content_type
            upload_bytes(path, content, content_type=mime)
            _append_artifact(
                db, document=document, artifact_type=artifact_type, status="stored", actor_user_id=actor_user_id,
                source_url=source_url, storage_path=path, content_type=mime, content=content,
            )
        except Exception as exc:
            _append_artifact(
                db, document=document, artifact_type=artifact_type, status="failed", actor_user_id=actor_user_id,
                source_url=source_url, error_message=str(exc)[:500],
            )

    # APISUNAT documents the PDF as a generated endpoint rather than a field
    # returned by getById. Build it only from the provider's document id and
    # our reserved file name; neither value is user-controlled at this point.
    if not document.provider_document_id:
        _append_artifact(
            db, document=document, artifact_type="pdf", status="unavailable", actor_user_id=actor_user_id,
            error_message="El proveedor aún no devolvió el identificador para generar el PDF",
        )
        return
    base = str(get_settings().apisunat_base_url).rstrip("/")
    pdf_url = f"{base}/documents/{quote(document.provider_document_id, safe='')}/getPDF/A4/{quote(document.file_name, safe='')}.pdf"
    try:
        content, response_content_type = _download_provider_artifact(pdf_url)
        path = _artifact_path(document, "pdf", "pdf")
        mime = response_content_type if response_content_type not in {"", "application/octet-stream"} else "application/pdf"
        upload_bytes(path, content, content_type=mime)
        _append_artifact(
            db, document=document, artifact_type="pdf", status="stored", actor_user_id=actor_user_id,
            source_url=pdf_url, storage_path=path, content_type=mime, content=content,
        )
    except Exception as exc:
        _append_artifact(
            db, document=document, artifact_type="pdf", status="failed", actor_user_id=actor_user_id,
            source_url=pdf_url, error_message=str(exc)[:500],
        )
