from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.enterprise import EnterpriseRecord, EnterpriseRecordEvent
from app.schemas.enterprise import RecordCreate, RecordEdit, RecordTransition
from app.security.auth import Actor, get_actor
from app.services import enterprise as service
from app.services.idempotency import claim, complete, replay_response

router = APIRouter(prefix="/enterprise", tags=["enterprise suite"])


@router.get("/catalog")
def catalog(actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    return service.catalog(actor, db)


@router.get("/{resource_code}/options/{field_key}")
def options(resource_code: str, field_key: str, q: str = Query(default="", max_length=80), selected: UUID | None = None, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    resource = service.authorize(resource_code, "read", actor, db)
    return service.options(resource, field_key, actor, db, q, selected)


@router.get("/{resource_code}/records")
def records(resource_code: str, q: str = Query(default="", max_length=100), page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100), actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    service.authorize(resource_code, "read", actor, db); service.ensure_ready(db)
    criteria = [EnterpriseRecord.tenant_id == actor.tenant_id, EnterpriseRecord.resource_code == resource_code]
    if q.strip(): criteria.append(EnterpriseRecord.number.ilike(f"%{q.strip()}%") | EnterpriseRecord.title.ilike(f"%{q.strip()}%"))
    total = db.scalar(select(func.count()).select_from(EnterpriseRecord).where(*criteria)) or 0
    rows = db.scalars(select(EnterpriseRecord).where(*criteria).order_by(EnterpriseRecord.created_at.desc(), EnterpriseRecord.id).limit(page_size).offset((page - 1) * page_size)).all()
    return {"records": [service.payload(row) for row in rows], "meta": {"page": page, "page_size": page_size, "total": total, "total_pages": (total + page_size - 1) // page_size, "has_previous": page > 1, "has_next": page * page_size < total}}


@router.get("/records/{record_id}")
def get_record(record_id: UUID, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    service.ensure_ready(db); row = service.get_record(db, actor, record_id)
    service.authorize(row.resource_code, "read", actor, db)
    events = db.scalars(select(EnterpriseRecordEvent).where(EnterpriseRecordEvent.tenant_id == actor.tenant_id, EnterpriseRecordEvent.record_id == row.id).order_by(EnterpriseRecordEvent.version)).all()
    return {**service.payload(row), "history": [{key: getattr(item, key) for key in ("version", "action", "from_state", "to_state", "note", "actor_user_id", "created_at", "content_hash")} for item in events]}


def _command(request, actor, db, operation, body, perform):
    try:
        replay = claim(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, operation=operation, key=request.headers.get("Idempotency-Key"), payload=body)
        if replay and replay.is_replay: return replay_response(replay)
        row = perform()
        response = jsonable_encoder(service.payload(row))
        complete(db, claim=replay, response_body=response, response_status=201 if operation.endswith(".create") else 200)
        db.commit()
        return response
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="Código o asignación en conflicto. No se guardaron cambios") from error
    except Exception:
        db.rollback(); raise


@router.post("/{resource_code}/records", status_code=status.HTTP_201_CREATED)
def create_record(resource_code: str, body: RecordCreate, request: Request, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    resource = service.authorize(resource_code, "write", actor, db); service.ensure_ready(db)
    return _command(request, actor, db, f"enterprise.{resource_code}.create", body.model_dump(mode="json"), lambda: service.create(resource, body, actor, db))


@router.post("/records/{record_id}/edit")
def edit_record(record_id: UUID, body: RecordEdit, request: Request, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    service.ensure_ready(db); row = service.get_record(db, actor, record_id, lock=True)
    resource = service.authorize(row.resource_code, "write", actor, db)
    return _command(request, actor, db, f"enterprise.{row.resource_code}.edit", {"id": str(row.id), **body.model_dump(mode="json")}, lambda: service.edit(row, resource, body, actor, db))


@router.post("/records/{record_id}/transition")
def transition_record(record_id: UUID, body: RecordTransition, request: Request, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    service.ensure_ready(db); row = service.get_record(db, actor, record_id, lock=True)
    resource = service.authorize(row.resource_code, "write", actor, db)
    return _command(request, actor, db, f"enterprise.{row.resource_code}.transition", {"id": str(row.id), **body.model_dump(mode="json")}, lambda: service.transition(row, resource, body, actor, db))
