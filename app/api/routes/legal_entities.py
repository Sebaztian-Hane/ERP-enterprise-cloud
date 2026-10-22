"""Tenant-scoped taxpayer and branch master data.

The endpoints are intentionally separate from tenant administration: an ERP
subscription can contain multiple legal entities, while only configuration
roles may alter tax identities and operational locations.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import Branch, LegalEntity
from app.schemas.legal_entities import BranchCreate, BranchOut, LegalEntityCreate, LegalEntityOut
from app.security.auth import Actor, require_permission
from app.services.audit import record


router = APIRouter(prefix="/legal-entities", tags=["legal-entities"])


@router.get("", response_model=list[LegalEntityOut])
def list_legal_entities(actor: Actor = Depends(require_permission("configuration:read")), db: Session = Depends(get_db)):
    return db.scalars(select(LegalEntity).where(LegalEntity.tenant_id == actor.tenant_id).order_by(LegalEntity.is_default.desc(), LegalEntity.legal_name)).all()


@router.post("", response_model=LegalEntityOut, status_code=status.HTTP_201_CREATED)
def create_legal_entity(data: LegalEntityCreate, actor: Actor = Depends(require_permission("configuration:write")), db: Session = Depends(get_db)):
    # The first issuer becomes the default. Setting a new default atomically
    # clears the previous one before the partial unique index is checked.
    has_entity = db.scalar(select(LegalEntity.id).where(LegalEntity.tenant_id == actor.tenant_id).limit(1)) is not None
    if data.is_default or not has_entity:
        db.execute(update(LegalEntity).where(LegalEntity.tenant_id == actor.tenant_id).values(is_default=False))
    values = data.model_dump()
    values["is_default"] = data.is_default or not has_entity
    entity = LegalEntity(tenant_id=actor.tenant_id, **values)
    db.add(entity)
    try:
        db.flush()
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="create", module="configuration", entity="legal_entity", entity_id=entity.id, detail=entity.tax_id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ya existe una entidad legal con ese código o RUC en la empresa") from exc
    db.refresh(entity)
    return entity


@router.get("/branches", response_model=list[BranchOut])
def list_branches(actor: Actor = Depends(require_permission("configuration:read")), db: Session = Depends(get_db)):
    return db.scalars(select(Branch).where(Branch.tenant_id == actor.tenant_id).order_by(Branch.name)).all()


@router.post("/branches", response_model=BranchOut, status_code=status.HTTP_201_CREATED)
def create_branch(data: BranchCreate, actor: Actor = Depends(require_permission("configuration:write")), db: Session = Depends(get_db)):
    entity = db.scalar(select(LegalEntity.id).where(LegalEntity.id == data.legal_entity_id, LegalEntity.tenant_id == actor.tenant_id, LegalEntity.is_active.is_(True)))
    if not entity:
        raise HTTPException(status_code=404, detail="La entidad legal no pertenece a la empresa activa")
    branch = Branch(tenant_id=actor.tenant_id, **data.model_dump())
    db.add(branch)
    try:
        db.flush()
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="create", module="configuration", entity="branch", entity_id=branch.id, detail=branch.code)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ya existe una sucursal con ese código para la entidad legal") from exc
    db.refresh(branch)
    return branch
