from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import Supplier
from app.schemas.catalog import SupplierCreate, SupplierOut, SupplierUpdate
from app.schemas.pagination import paginate
from app.security.auth import Actor, require_permission
from app.services.audit import record
from app.core.config import get_settings
from app.services.decolecta import DecolectaError, lookup_tax_id

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


@router.get("")
def list_suppliers(
    actor: Actor = Depends(require_permission("suppliers:read")),
    db: Session = Depends(get_db),
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=100),
):
    statement = select(Supplier).where(Supplier.tenant_id == actor.tenant_id).order_by(Supplier.name, Supplier.id)
    if not isinstance(page, int) and not isinstance(page_size, int):
        return db.scalars(statement).all()
    return paginate(db, statement, key="suppliers", page=page or 1, page_size=page_size or 25)


@router.get("/lookup/{tax_id}")
def lookup_supplier_ruc(tax_id: str, actor: Actor = Depends(require_permission("suppliers:read")), db: Session = Depends(get_db)):
    """Prefill a supplier from SUNAT and identify a prior supplier by RUC."""
    normalized = tax_id.strip()
    if not normalized.isdigit() or len(normalized) != 11:
        raise HTTPException(status_code=422, detail="Ingresa un RUC de 11 dígitos")
    existing = db.scalar(select(Supplier).where(Supplier.tenant_id == actor.tenant_id, Supplier.tax_id == normalized))
    if existing:
        return {
            "existing_supplier_id": str(existing.id),
            "tax_id": existing.tax_id,
            "name": existing.name,
            "email": existing.email or "",
            "phone": existing.phone or "",
            "address": existing.address or "",
            "document_type": "RUC",
            "source": "erp",
        }
    try:
        result = lookup_tax_id(get_settings(), normalized)
    except DecolectaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result["document_type"] != "RUC":
        raise HTTPException(status_code=422, detail="Ingresa un RUC de 11 dígitos")
    return {"existing_supplier_id": None, **result, "source": "decolecta"}


@router.post("", response_model=SupplierOut, status_code=status.HTTP_201_CREATED)
def create_supplier(data: SupplierCreate, actor: Actor = Depends(require_permission("suppliers:write")), db: Session = Depends(get_db)):
    supplier = Supplier(tenant_id=actor.tenant_id, **data.model_dump())
    db.add(supplier)
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="create", module="suppliers", entity="supplier", entity_id=supplier.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ya existe un proveedor con ese código o RUC en esta empresa") from exc
    db.refresh(supplier)
    return supplier


@router.patch("/{supplier_id}", response_model=SupplierOut)
def update_supplier(supplier_id: UUID, data: SupplierUpdate, actor: Actor = Depends(require_permission("suppliers:write")), db: Session = Depends(get_db)):
    supplier = db.scalar(select(Supplier).where(Supplier.id == supplier_id, Supplier.tenant_id == actor.tenant_id))
    if not supplier:
        raise HTTPException(status_code=404, detail="Proveedor no encontrado en la empresa activa")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(supplier, field, value)
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="update", module="suppliers", entity="supplier", entity_id=supplier.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ya existe un proveedor con ese código o RUC en esta empresa") from exc
    db.refresh(supplier)
    return supplier
