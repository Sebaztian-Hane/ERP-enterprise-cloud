from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import Category, Inventory, InventoryLot, InventoryMovement, InventorySerial, Product, UnitOfMeasure
from app.schemas.catalog import CategoryCreate, CategoryOut, ProductCreate, ProductOut, ProductUpdate
from app.schemas.pagination import paginate
from app.security.auth import Actor, require_permission
from app.services.audit import record

router = APIRouter(prefix="/products", tags=["products"])


def _resolve_unit(db: Session, *, tenant_id: UUID, unit_id: UUID) -> UnitOfMeasure:
    unit = db.scalar(select(UnitOfMeasure).where(
        UnitOfMeasure.id == unit_id,
        UnitOfMeasure.tenant_id == tenant_id,
        UnitOfMeasure.is_active.is_(True),
    ))
    if unit is None:
        raise HTTPException(status_code=404, detail="Unidad activa no encontrada en la empresa")
    return unit


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(actor: Actor = Depends(require_permission("products:read")), db: Session = Depends(get_db)):
    return db.scalars(select(Category).where(Category.tenant_id == actor.tenant_id).order_by(Category.name)).all()


@router.get("/units")
def list_units(actor: Actor = Depends(require_permission("products:read")), db: Session = Depends(get_db)):
    return db.scalars(select(UnitOfMeasure).where(
        UnitOfMeasure.tenant_id == actor.tenant_id,
        UnitOfMeasure.is_active.is_(True),
    ).order_by(UnitOfMeasure.code)).all()


@router.post("/categories", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
def create_category(data: CategoryCreate, actor: Actor = Depends(require_permission("products:write")), db: Session = Depends(get_db)):
    category = Category(tenant_id=actor.tenant_id, **data.model_dump())
    db.add(category)
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="create", module="products", entity="category", entity_id=category.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="La categoría ya existe en esta empresa") from exc
    db.refresh(category)
    return category


@router.get("")
def list_products(
    actor: Actor = Depends(require_permission("products:read")),
    db: Session = Depends(get_db),
    q: str | None = Query(default=None, min_length=1, max_length=120),
    active: bool | None = Query(default=None),
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=100),
):
    statement = select(Product).where(Product.tenant_id == actor.tenant_id).order_by(Product.name, Product.id)
    if q:
        pattern = f"%{q.strip()}%"
        statement = statement.where(or_(Product.name.ilike(pattern), Product.sku.ilike(pattern)))
    if active is not None:
        statement = statement.where(Product.is_active.is_(active))
    if not isinstance(page, int) and not isinstance(page_size, int):
        return db.scalars(statement).all()
    return paginate(db, statement, key="products", page=page or 1, page_size=page_size or 25)


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(data: ProductCreate, actor: Actor = Depends(require_permission("products:write")), db: Session = Depends(get_db)):
    if data.category_id and not db.scalar(select(Category.id).where(Category.id == data.category_id, Category.tenant_id == actor.tenant_id)):
        raise HTTPException(status_code=404, detail="Categoría no encontrada en la empresa activa")
    unit = _resolve_unit(db, tenant_id=actor.tenant_id, unit_id=data.unit_id)
    values = data.model_dump()
    product = Product(
        tenant_id=actor.tenant_id,
        **values,
        unit=unit.code,
        unit_code=unit.code,
        unit_name=unit.name,
    )
    db.add(product)
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="create", module="products", entity="product", entity_id=product.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El SKU ya existe en esta empresa") from exc
    db.refresh(product)
    return product


@router.patch("/{product_id}", response_model=ProductOut)
def update_product(product_id: UUID, data: ProductUpdate, actor: Actor = Depends(require_permission("products:write")), db: Session = Depends(get_db)):
    product = db.scalar(select(Product).where(Product.id == product_id, Product.tenant_id == actor.tenant_id))
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado en la empresa activa")
    changes = data.model_dump(exclude_unset=True)
    if changes.get("category_id") and not db.scalar(select(Category.id).where(Category.id == changes["category_id"], Category.tenant_id == actor.tenant_id)):
        raise HTTPException(status_code=404, detail="Categoría no encontrada en la empresa activa")
    if "tracking_mode" in changes and changes["tracking_mode"] != product.tracking_mode:
        has_stock = db.scalar(select(Inventory.id).where(
            Inventory.tenant_id == actor.tenant_id,
            Inventory.product_id == product.id,
            Inventory.quantity != 0,
        ).limit(1))
        has_evidence = db.scalar(select(InventoryLot.id).where(
            InventoryLot.tenant_id == actor.tenant_id,
            InventoryLot.product_id == product.id,
        ).limit(1)) or db.scalar(select(InventorySerial.id).where(
            InventorySerial.tenant_id == actor.tenant_id,
            InventorySerial.product_id == product.id,
        ).limit(1))
        if has_stock or has_evidence:
            raise HTTPException(status_code=409, detail="No se puede cambiar el control de trazabilidad de un producto con existencias o historial")
    if "unit_id" in changes and changes["unit_id"] != product.unit_id:
        has_stock = db.scalar(select(Inventory.id).where(
            Inventory.tenant_id == actor.tenant_id,
            Inventory.product_id == product.id,
            Inventory.quantity != 0,
        ).limit(1))
        has_history = db.scalar(select(InventoryMovement.id).where(
            InventoryMovement.tenant_id == actor.tenant_id,
            InventoryMovement.product_id == product.id,
        ).limit(1))
        if has_stock or has_history:
            raise HTTPException(status_code=409, detail="No se puede cambiar la unidad de un producto con existencias o movimientos; crea un producto nuevo o realiza una conversión controlada")
        unit = _resolve_unit(db, tenant_id=actor.tenant_id, unit_id=changes["unit_id"])
        changes.update(unit=unit.code, unit_code=unit.code, unit_name=unit.name)
    for field, value in changes.items():
        setattr(product, field, value)
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="update", module="products", entity="product", entity_id=product.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El SKU ya existe en esta empresa") from exc
    db.refresh(product)
    return product
