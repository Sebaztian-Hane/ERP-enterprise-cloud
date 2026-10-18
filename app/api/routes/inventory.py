from decimal import Decimal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import Inventory, InventoryMovement, InventoryTransfer, Product, Warehouse
from app.schemas.inventory import InventoryMovementOut, InventoryOut, InventoryTransferCreate, InventoryTransferOut, StockAdjustment, WarehouseCreate, WarehouseOut, WarehouseUpdate
from app.security.auth import Actor, require_permission
from app.services.audit import record
from app.services.stock import change_stock

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get("/warehouses", response_model=list[WarehouseOut])
def list_warehouses(actor: Actor = Depends(require_permission("inventory:read")), db: Session = Depends(get_db)):
    return db.scalars(select(Warehouse).where(Warehouse.tenant_id == actor.tenant_id).order_by(Warehouse.name)).all()


@router.post("/warehouses", response_model=WarehouseOut, status_code=status.HTTP_201_CREATED)
def create_warehouse(data: WarehouseCreate, actor: Actor = Depends(require_permission("inventory:write")), db: Session = Depends(get_db)):
    warehouse = Warehouse(tenant_id=actor.tenant_id, **data.model_dump())
    db.add(warehouse)
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="create", module="inventory", entity="warehouse", entity_id=warehouse.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El código de almacén ya existe en esta empresa") from exc
    db.refresh(warehouse)
    return warehouse


@router.patch("/warehouses/{warehouse_id}", response_model=WarehouseOut)
def update_warehouse(warehouse_id: UUID, data: WarehouseUpdate, actor: Actor = Depends(require_permission("inventory:write")), db: Session = Depends(get_db)):
    warehouse = db.scalar(select(Warehouse).where(Warehouse.id == warehouse_id, Warehouse.tenant_id == actor.tenant_id))
    if not warehouse:
        raise HTTPException(status_code=404, detail="Almacén no encontrado en la empresa activa")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(warehouse, field, value)
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="update", module="inventory", entity="warehouse", entity_id=warehouse.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El código de almacén ya existe en esta empresa") from exc
    db.refresh(warehouse)
    return warehouse


@router.get("/stock", response_model=list[InventoryOut])
def list_stock(actor: Actor = Depends(require_permission("inventory:read")), db: Session = Depends(get_db)):
    return db.scalars(select(Inventory).where(Inventory.tenant_id == actor.tenant_id).order_by(Inventory.product_id, Inventory.warehouse_id)).all()


@router.get("/movements", response_model=list[InventoryMovementOut])
def list_movements(actor: Actor = Depends(require_permission("inventory:read")), db: Session = Depends(get_db)):
    return db.scalars(select(InventoryMovement).where(InventoryMovement.tenant_id == actor.tenant_id).order_by(InventoryMovement.created_at.desc())).all()


@router.post("/adjustments", response_model=InventoryMovementOut, status_code=status.HTTP_201_CREATED)
def adjust_stock(data: StockAdjustment, actor: Actor = Depends(require_permission("inventory:write")), db: Session = Depends(get_db)):
    product = db.scalar(select(Product).where(Product.id == data.product_id, Product.tenant_id == actor.tenant_id, Product.is_active.is_(True)))
    warehouse = db.scalar(select(Warehouse).where(Warehouse.id == data.warehouse_id, Warehouse.tenant_id == actor.tenant_id, Warehouse.is_active.is_(True)))
    if not product or not warehouse:
        raise HTTPException(status_code=404, detail="Producto o almacén no encontrado en la empresa activa")
    stock = db.scalar(select(Inventory).where(Inventory.tenant_id == actor.tenant_id, Inventory.product_id == data.product_id, Inventory.warehouse_id == data.warehouse_id).with_for_update())
    if stock is None:
        stock = Inventory(tenant_id=actor.tenant_id, product_id=data.product_id, warehouse_id=data.warehouse_id, quantity=Decimal("0"))
        db.add(stock)
    if stock.quantity + data.quantity < 0:
        raise HTTPException(status_code=422, detail="El ajuste dejaría la existencia en negativo")
    stock.quantity += data.quantity
    movement = InventoryMovement(tenant_id=actor.tenant_id, product_id=data.product_id, warehouse_id=data.warehouse_id, movement_type="adjustment", quantity=data.quantity, reference=data.reference, note=data.note)
    db.add(movement)
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="adjust", module="inventory", entity="inventory_movement", entity_id=movement.id)
    db.commit()
    db.refresh(movement)
    return movement


@router.post("/transfers", response_model=InventoryTransferOut, status_code=status.HTTP_201_CREATED)
def transfer_stock(data: InventoryTransferCreate, actor: Actor = Depends(require_permission("inventory:write")), db: Session = Depends(get_db)):
    product = db.scalar(select(Product.id).where(Product.id == data.product_id, Product.tenant_id == actor.tenant_id, Product.is_active.is_(True)))
    warehouses = set(db.scalars(select(Warehouse.id).where(Warehouse.id.in_({data.source_warehouse_id, data.destination_warehouse_id}), Warehouse.tenant_id == actor.tenant_id, Warehouse.is_active.is_(True))).all())
    if not product or warehouses != {data.source_warehouse_id, data.destination_warehouse_id}:
        raise HTTPException(status_code=404, detail="Producto o almacén no encontrado en la empresa activa")
    transfer = InventoryTransfer(tenant_id=actor.tenant_id, **data.model_dump())
    db.add(transfer)
    try:
        db.flush()
        change_stock(db, tenant_id=actor.tenant_id, product_id=data.product_id, warehouse_id=data.source_warehouse_id, quantity=-data.quantity, movement_type="transfer_out", reference=data.number)
        change_stock(db, tenant_id=actor.tenant_id, product_id=data.product_id, warehouse_id=data.destination_warehouse_id, quantity=data.quantity, movement_type="transfer_in", reference=data.number)
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="transfer", module="inventory", entity="inventory_transfer", entity_id=transfer.id)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El número de transferencia ya existe en esta empresa") from exc
    db.refresh(transfer)
    return transfer
