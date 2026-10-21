from decimal import Decimal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import Inventory, InventoryMovement, InventoryTransfer, Product, Warehouse
from app.schemas.inventory import InventoryMovementOut, InventoryOut, InventoryTransferCreate, InventoryTransferOut, StockAdjustment, WarehouseCreate, WarehouseOut, WarehouseUpdate
from app.schemas.pagination import paginate
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


@router.get("/stock")
def list_stock(
    actor: Actor = Depends(require_permission("inventory:read")),
    db: Session = Depends(get_db),
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=100),
):
    statement = select(Inventory).where(Inventory.tenant_id == actor.tenant_id).order_by(Inventory.product_id, Inventory.warehouse_id)
    if not isinstance(page, int) and not isinstance(page_size, int):
        return db.scalars(statement).all()
    return paginate(db, statement, key="stock", page=page or 1, page_size=page_size or 25)


@router.get("/movements")
def list_movements(
    actor: Actor = Depends(require_permission("inventory:read")),
    db: Session = Depends(get_db),
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=100),
):
    statement = select(InventoryMovement).where(InventoryMovement.tenant_id == actor.tenant_id).order_by(InventoryMovement.created_at.desc(), InventoryMovement.id)
    if not isinstance(page, int) and not isinstance(page_size, int):
        return db.scalars(statement).all()
    return paginate(db, statement, key="movements", page=page or 1, page_size=page_size or 25)


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
    # Creation only records the intention.  Stock leaves the source warehouse
    # at dispatch and reaches the destination at receipt, preserving transit.
    transfer = InventoryTransfer(tenant_id=actor.tenant_id, status="draft", **data.model_dump())
    db.add(transfer)
    try:
        db.flush()
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="create_transfer", module="inventory", entity="inventory_transfer", entity_id=transfer.id)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El número de transferencia ya existe en esta empresa") from exc
    db.refresh(transfer)
    return transfer


@router.get("/transfers")
def list_transfers(
    actor: Actor = Depends(require_permission("inventory:read")),
    db: Session = Depends(get_db),
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=100),
):
    statement = select(InventoryTransfer).where(
        InventoryTransfer.tenant_id == actor.tenant_id,
    ).order_by(InventoryTransfer.created_at.desc(), InventoryTransfer.id)
    if not isinstance(page, int) and not isinstance(page_size, int):
        return db.scalars(statement).all()
    return paginate(db, statement, key="transfers", page=page or 1, page_size=page_size or 25)


@router.post("/transfers/{transfer_id}/dispatch", response_model=InventoryTransferOut)
def dispatch_transfer(transfer_id: UUID, actor: Actor = Depends(require_permission("inventory:write")), db: Session = Depends(get_db)):
    transfer = db.scalar(select(InventoryTransfer).where(
        InventoryTransfer.id == transfer_id,
        InventoryTransfer.tenant_id == actor.tenant_id,
    ).with_for_update())
    if not transfer or transfer.status != "draft":
        raise HTTPException(status_code=409, detail="La transferencia debe estar en borrador para despacharse")
    try:
        change_stock(db, tenant_id=actor.tenant_id, product_id=transfer.product_id, warehouse_id=transfer.source_warehouse_id,
                     quantity=-transfer.quantity, movement_type="transfer_out", reference=transfer.number)
        transfer.status = "in_transit"
        db.execute(__import__("sqlalchemy").text("update inventory_transfers set dispatched_by=:actor, dispatched_at=now() where id=:id and tenant_id=:tenant"), {"actor": str(actor.user_id), "id": str(transfer.id), "tenant": str(actor.tenant_id)})
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="dispatch_transfer", module="inventory", entity="inventory_transfer", entity_id=transfer.id)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    db.refresh(transfer)
    return transfer


@router.post("/transfers/{transfer_id}/receive", response_model=InventoryTransferOut)
def receive_transfer(transfer_id: UUID, actor: Actor = Depends(require_permission("inventory:write")), db: Session = Depends(get_db)):
    transfer = db.scalar(select(InventoryTransfer).where(
        InventoryTransfer.id == transfer_id,
        InventoryTransfer.tenant_id == actor.tenant_id,
    ).with_for_update())
    if not transfer or transfer.status != "in_transit":
        raise HTTPException(status_code=409, detail="La transferencia debe estar en tránsito para recibirse")
    change_stock(db, tenant_id=actor.tenant_id, product_id=transfer.product_id, warehouse_id=transfer.destination_warehouse_id,
                 quantity=transfer.quantity, movement_type="transfer_in", reference=transfer.number)
    transfer.status = "completed"
    db.execute(__import__("sqlalchemy").text("update inventory_transfers set received_by=:actor, received_at=now() where id=:id and tenant_id=:tenant"), {"actor": str(actor.user_id), "id": str(transfer.id), "tenant": str(actor.tenant_id)})
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="receive_transfer", module="inventory", entity="inventory_transfer", entity_id=transfer.id)
    db.commit()
    db.refresh(transfer)
    return transfer
