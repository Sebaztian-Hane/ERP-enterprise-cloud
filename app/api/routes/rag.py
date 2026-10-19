"""Private endpoints used by the n8n RAG integration.

The browser never receives the RAG integration credential nor calls n8n directly.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.security.auth import Actor, get_actor
from app.services.rag_sync import (
    export_tenant_documents,
    require_rag_integration_key,
    signed_request,
)


router = APIRouter(prefix="/rag", tags=["RAG"])


@router.get("/tenants", dependencies=[Depends(require_rag_integration_key)])
def list_rag_tenants(db: Session = Depends(get_db)):
    """List active companies for the scheduled n8n re-indexing workflow."""
    rows = db.execute(text("select id, name, timezone from tenants where is_active = true order by name")).mappings()
    return {"tenants": [{"id": str(row["id"]), "name": row["name"], "timezone": row["timezone"]} for row in rows]}


@router.get("/export", dependencies=[Depends(require_rag_integration_key)])
def export_rag_documents(tenant_id: UUID, db: Session = Depends(get_db)):
    """Export safe, normalized business facts for one tenant only."""
    return {
        "tenant_id": str(tenant_id),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "documents": export_tenant_documents(db, tenant_id),
    }


class RAGChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2_000)
    conversation_id: str | None = Field(default=None, max_length=100)


@router.post("/chat")
def chat_with_rag(payload: RAGChatRequest, actor: Actor = Depends(get_actor)):
    """JWT-protected bridge from the ERP UI to the private n8n RAG workflow."""
    settings = get_settings()
    if not settings.rag_chat_webhook_url or not settings.rag_chat_webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El asistente ERP aún no está configurado",
        )
    event = {
        "message": payload.message.strip(),
        "tenant_id": str(actor.tenant_id),
        "user_id": str(actor.user_id),
        "role_code": actor.role_code,
        "conversation_id": payload.conversation_id,
    }
    try:
        body, headers = signed_request(event, settings.rag_chat_webhook_secret)
        with httpx.Client(timeout=35) as client:
            response = client.post(
                str(settings.rag_chat_webhook_url),
                content=body,
                headers=headers,
            )
            response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="El asistente tardó demasiado en responder") from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail="El asistente no pudo procesar la consulta") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="No se pudo conectar con el asistente") from exc
    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="El asistente respondió con un formato inválido") from exc
