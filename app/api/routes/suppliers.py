from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import Supplier
from app.schemas.catalog import SupplierCreate, SupplierOut
from app.security.auth import Actor, require_permission
from app.services.audit import record

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


@router.get("", response_model=list[SupplierOut])
def list_suppliers(actor: Actor = Depends(require_permission("suppliers:read")), db: Session = Depends(get_db)):
    return db.scalars(select(Supplier).where(Supplier.tenant_id == actor.tenant_id).order_by(Supplier.name)).all()


@router.post("", response_model=SupplierOut, status_code=status.HTTP_201_CREATED)
def create_supplier(data: SupplierCreate, actor: Actor = Depends(require_permission("suppliers:write")), db: Session = Depends(get_db)):
    supplier = Supplier(tenant_id=actor.tenant_id, **data.model_dump())
    db.add(supplier)
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="create", module="suppliers", entity="supplier", entity_id=supplier.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El código de proveedor ya existe en esta empresa") from exc
    db.refresh(supplier)
    return supplier
