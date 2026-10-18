from pathlib import PurePath
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.models.entities import Document
from app.schemas.documents import DocumentOut, SignedUrlOut
from app.security.auth import Actor, require_permission
from app.services.audit import record
from app.storage.supabase import signed_url, upload

router = APIRouter(prefix="/documents", tags=["documents"])
ALLOWED_TYPES = {"application/pdf", "image/jpeg", "image/png", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


@router.get("", response_model=list[DocumentOut])
def list_documents(actor: Actor = Depends(require_permission("documents:read")), db: Session = Depends(get_db)):
    return db.scalars(select(Document).where(Document.tenant_id == actor.tenant_id).order_by(Document.created_at.desc())).all()


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def upload_document(request: Request, file: UploadFile = File(...), entity_type: str | None = Form(None), entity_id: UUID | None = Form(None), actor: Actor = Depends(require_permission("documents:write")), db: Session = Depends(get_db)):
    settings = get_settings()
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=415, detail="Tipo de archivo no permitido")
    content = await file.read(settings.max_document_size_bytes + 1)
    if not content or len(content) > settings.max_document_size_bytes:
        raise HTTPException(status_code=413, detail="El archivo excede el tamaño permitido")
    filename = PurePath(file.filename or "documento").name
    document_id = uuid4()
    path = f"tenant/{actor.tenant_id}/{document_id}/{filename}"
    await upload(path, file, content)
    document = Document(id=document_id, tenant_id=actor.tenant_id, original_filename=filename, storage_path=path, content_type=file.content_type, size_bytes=len(content), entity_type=entity_type, entity_id=entity_id, uploaded_by=actor.user_id)
    db.add(document)
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="upload", module="documents", entity="document", entity_id=document.id)
    db.commit()
    db.refresh(document)
    return document


@router.post("/{document_id}/signed-url", response_model=SignedUrlOut)
@limiter.limit("30/minute")
async def create_download_url(request: Request, document_id: UUID, actor: Actor = Depends(require_permission("documents:read")), db: Session = Depends(get_db)):
    document = db.scalar(select(Document).where(Document.id == document_id, Document.tenant_id == actor.tenant_id))
    if not document:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    url = await signed_url(document.storage_path)
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="download_url", module="documents", entity="document", entity_id=document.id)
    db.commit()
    return SignedUrlOut(url=url, expires_in=300)
