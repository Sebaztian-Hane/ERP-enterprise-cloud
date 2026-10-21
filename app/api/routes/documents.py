from pathlib import PurePath
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.models.entities import Document
from app.schemas.documents import DocumentOut, SignedUrlOut
from app.schemas.pagination import paginate
from app.security.auth import Actor, require_permission
from app.services.audit import record
from app.storage.supabase import signed_url, upload

router = APIRouter(prefix="/documents", tags=["documents"])
ALLOWED_TYPES = {"application/pdf", "image/jpeg", "image/png", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


@router.get("")
def list_documents(
    actor: Actor = Depends(require_permission("documents:read")),
    db: Session = Depends(get_db),
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=100),
):
    statement = select(Document).where(Document.tenant_id == actor.tenant_id).order_by(Document.created_at.desc(), Document.id)
    if not isinstance(page, int) and not isinstance(page_size, int):
        return db.scalars(statement).all()
    return paginate(db, statement, key="documents", page=page or 1, page_size=page_size or 25)


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def upload_document(request: Request, response: Response, file: UploadFile = File(...), entity_type: str | None = Form(None), entity_id: UUID | None = Form(None), actor: Actor = Depends(require_permission("documents:write")), db: Session = Depends(get_db)):
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
async def create_download_url(request: Request, response: Response, document_id: UUID, actor: Actor = Depends(require_permission("documents:read")), db: Session = Depends(get_db)):
    document = db.scalar(select(Document).where(Document.id == document_id, Document.tenant_id == actor.tenant_id))
    if not document:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    url = await signed_url(document.storage_path)
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="download_url", module="documents", entity="document", entity_id=document.id)
    db.commit()
    return SignedUrlOut(url=url, expires_in=300)


@router.get("/approval-records")
def list_approval_records(
    actor: Actor = Depends(require_permission("documents:read")),
    db: Session = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
):
    """List tamper-evident operational documents, scoped to the active tenant."""
    offset = (page - 1) * page_size
    total = db.execute(text("select count(*) from approval_documents where tenant_id=:tenant"), {"tenant": str(actor.tenant_id)}).scalar_one()
    records = db.execute(text("""
        select d.id, d.approval_request_id, d.module, d.entity_type, d.entity_id,
               d.document_number, d.version, d.status, d.content_hash, d.created_at, d.updated_at,
               coalesce(jsonb_agg(jsonb_build_object(
                 'id', s.id, 'type', s.signature_type, 'decision', s.decision,
                 'signed_hash', s.signed_hash, 'signed_at', s.signed_at,
                 'certificate_subject', s.certificate_subject, 'certificate_issuer', s.certificate_issuer,
                 'certificate_serial', s.certificate_serial, 'provider_reference', s.provider_reference,
                 'signature_algorithm', s.signature_algorithm
               ) order by s.created_at) filter (where s.id is not null), '[]'::jsonb) as signatures
        from approval_documents d
        left join approval_signatures s on s.approval_document_id=d.id and s.tenant_id=d.tenant_id
        where d.tenant_id=:tenant
        group by d.id order by d.created_at desc, d.id
        limit :limit offset :offset
    """), {"tenant": str(actor.tenant_id), "limit": page_size, "offset": offset}).mappings().all()
    return {"approval_records": records, "meta": {"page": page, "page_size": page_size, "total": total, "total_pages": max(1, (total + page_size - 1) // page_size)}}


@router.get("/approval-records/{record_id}")
def approval_record(record_id: UUID, actor: Actor = Depends(require_permission("documents:read")), db: Session = Depends(get_db)):
    record_row = db.execute(text("""
        select d.*, coalesce(jsonb_agg(jsonb_build_object(
          'id', s.id, 'type', s.signature_type, 'decision', s.decision,
          'signed_hash', s.signed_hash, 'signed_at', s.signed_at,
          'certificate_subject', s.certificate_subject, 'certificate_issuer', s.certificate_issuer,
          'certificate_serial', s.certificate_serial, 'provider_reference', s.provider_reference,
          'signature_algorithm', s.signature_algorithm
        ) order by s.created_at) filter (where s.id is not null), '[]'::jsonb) as signatures
        from approval_documents d
        left join approval_signatures s on s.approval_document_id=d.id and s.tenant_id=d.tenant_id
        where d.id=:id and d.tenant_id=:tenant group by d.id
    """), {"id": str(record_id), "tenant": str(actor.tenant_id)}).mappings().first()
    if not record_row:
        raise HTTPException(status_code=404, detail="Constancia documental no encontrada")
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="view_evidence", module="documents", entity="approval_document", entity_id=record_id)
    db.commit()
    return record_row
