"""Private endpoints used by the n8n RAG integration.

The browser never receives the RAG integration credential nor calls n8n directly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.models.entities import AssistantConversation, AssistantMessage
from app.security.auth import Actor, get_actor
from app.services.rag_sync import (
    export_tenant_documents,
    require_rag_integration_key,
    signed_request,
)


router = APIRouter(prefix="/rag", tags=["RAG"])
MAX_CONVERSATIONS_PER_MEMBER = 5


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
    conversation_id: UUID | None = None


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=160)


class AssistantMessageOut(BaseModel):
    id: UUID
    role: str
    content: str
    created_at: datetime


class ConversationOut(BaseModel):
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime
    preview: str | None = None


class ConversationDetailOut(ConversationOut):
    messages: list[AssistantMessageOut]


def _conversation_for_actor(conversation_id: UUID, actor: Actor, db: Session) -> AssistantConversation:
    conversation = db.scalar(
        select(AssistantConversation).where(
            AssistantConversation.id == conversation_id,
            AssistantConversation.tenant_id == actor.tenant_id,
            AssistantConversation.user_id == actor.user_id,
        )
    )
    if conversation is None:
        # Do not reveal whether a UUID exists in another tenant or belongs to another user.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="La conversación no existe")
    return conversation


def _conversation_out(conversation: AssistantConversation, preview: str | None = None) -> ConversationOut:
    return ConversationOut(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        preview=preview,
    )


def _safe_title(content: str) -> str:
    compact = " ".join(content.split())
    return compact[:157].rstrip() + ("…" if len(compact) > 157 else "")


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    conversations = db.scalars(
        select(AssistantConversation)
        .where(
            AssistantConversation.tenant_id == actor.tenant_id,
            AssistantConversation.user_id == actor.user_id,
        )
        .order_by(AssistantConversation.updated_at.desc())
        .limit(MAX_CONVERSATIONS_PER_MEMBER)
    ).all()
    result: list[ConversationOut] = []
    for conversation in conversations:
        preview = db.scalar(
            select(AssistantMessage.content)
            .where(AssistantMessage.conversation_id == conversation.id)
            .order_by(AssistantMessage.created_at.desc())
            .limit(1)
        )
        result.append(_conversation_out(conversation, preview))
    return result


@router.post("/conversations", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: ConversationCreate,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
):
    count = db.scalar(
        select(func.count()).select_from(AssistantConversation).where(
            AssistantConversation.tenant_id == actor.tenant_id,
            AssistantConversation.user_id == actor.user_id,
        )
    ) or 0
    if count >= MAX_CONVERSATIONS_PER_MEMBER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Alcanzaste el límite de 5 chats. Elimina uno para iniciar una nueva conversación.",
        )
    title = " ".join((payload.title or "").split()) or "Nueva conversación"
    conversation = AssistantConversation(tenant_id=actor.tenant_id, user_id=actor.user_id, title=title)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return _conversation_out(conversation)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailOut)
def get_conversation(conversation_id: UUID, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    conversation = _conversation_for_actor(conversation_id, actor, db)
    messages = db.scalars(
        select(AssistantMessage)
        .where(
            AssistantMessage.conversation_id == conversation.id,
            AssistantMessage.tenant_id == actor.tenant_id,
            AssistantMessage.user_id == actor.user_id,
        )
        .order_by(AssistantMessage.created_at.asc())
    ).all()
    return ConversationDetailOut(
        **_conversation_out(conversation).model_dump(),
        messages=[AssistantMessageOut(id=item.id, role=item.role, content=item.content, created_at=item.created_at) for item in messages],
    )


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(conversation_id: UUID, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    conversation = _conversation_for_actor(conversation_id, actor, db)
    db.delete(conversation)
    db.commit()


@router.post("/chat")
def chat_with_rag(
    payload: RAGChatRequest,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
):
    """JWT-protected bridge from the ERP UI to the private n8n RAG workflow."""
    if payload.conversation_id is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Selecciona o crea una conversación antes de enviar un mensaje")
    conversation = _conversation_for_actor(payload.conversation_id, actor, db)
    message = payload.message.strip()
    user_message = AssistantMessage(
        conversation_id=conversation.id,
        tenant_id=actor.tenant_id,
        user_id=actor.user_id,
        role="user",
        content=message,
    )
    db.add(user_message)
    if conversation.title == "Nueva conversación":
        conversation.title = _safe_title(message)
    conversation.updated_at = datetime.now(timezone.utc)
    # Persist the member's question before a remote workflow call. This keeps
    # history durable across n8n or network interruptions.
    db.commit()
    db.refresh(conversation)

    settings = get_settings()
    if not settings.rag_chat_webhook_url or not settings.rag_chat_webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El asistente ERP aún no está configurado",
        )

    history = db.execute(
        select(AssistantMessage.role, AssistantMessage.content)
        .where(AssistantMessage.conversation_id == conversation.id)
        .order_by(AssistantMessage.created_at.desc())
        .limit(12)
    ).all()
    event = {
        "message": message,
        "tenant_id": str(actor.tenant_id),
        "user_id": str(actor.user_id),
        "role_code": actor.role_code,
        "conversation_id": str(conversation.id),
        "history": [{"role": item.role, "content": item.content} for item in reversed(history)],
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
        result = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="El asistente respondió con un formato inválido") from exc
    answer = ""
    if isinstance(result, dict):
        for key in ("answer", "output", "message"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                answer = value.strip()
                break
    if answer:
        db.add(AssistantMessage(
            conversation_id=conversation.id,
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            role="assistant",
            content=answer,
        ))
        conversation.updated_at = datetime.now(timezone.utc)
        db.commit()
    return {"answer": answer or "No pude generar una respuesta para esa consulta.", "conversation_id": str(conversation.id)}
