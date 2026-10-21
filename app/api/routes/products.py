from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import Category, Product
from app.schemas.catalog import CategoryCreate, CategoryOut, ProductCreate, ProductOut, ProductUpdate
from app.schemas.pagination import paginate
from app.security.auth import Actor, require_permission
from app.services.audit import record

router = APIRouter(prefix="/products", tags=["products"])


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(actor: Actor = Depends(require_permission("products:read")), db: Session = Depends(get_db)):
    return db.scalars(select(Category).where(Category.tenant_id == actor.tenant_id).order_by(Category.name)).all()


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
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=100),
):
    statement = select(Product).where(Product.tenant_id == actor.tenant_id).order_by(Product.name, Product.id)
    if not isinstance(page, int) and not isinstance(page_size, int):
        return db.scalars(statement).all()
    return paginate(db, statement, key="products", page=page or 1, page_size=page_size or 25)


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(data: ProductCreate, actor: Actor = Depends(require_permission("products:write")), db: Session = Depends(get_db)):
    if data.category_id and not db.scalar(select(Category.id).where(Category.id == data.category_id, Category.tenant_id == actor.tenant_id)):
        raise HTTPException(status_code=404, detail="Categoría no encontrada en la empresa activa")
    product = Product(tenant_id=actor.tenant_id, **data.model_dump())
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
