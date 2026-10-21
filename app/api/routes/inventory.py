from decimal import Decimal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import Inventory, InventoryLot, InventoryMovement, InventorySerial, InventoryTransfer, Product, Warehouse
from app.schemas.inventory import InventoryLotReceive, InventoryMovementOut, InventoryOut, InventorySerialReceive, InventoryTransferCreate, InventoryTransferOut, StockAdjustment, WarehouseCreate, WarehouseOut, WarehouseUpdate
from app.schemas.pagination import paginate
from app.security.auth import Actor, require_permission
from app.services.audit import record
from app.services.stock import change_stock

router = APIRouter(prefix="/inventory", tags=["inventory"])


def _tracking_product(product_id: UUID, warehouse_id: UUID, mode: str, actor: Actor, db: Session) -> Product:
    product = db.scalar(select(Product).where(
        Product.id == product_id,
        Product.tenant_id == actor.tenant_id,
        Product.is_active.is_(True),
    ).with_for_update())
    warehouse = db.scalar(select(Warehouse.id).where(
        Warehouse.id == warehouse_id,
        Warehouse.tenant_id == actor.tenant_id,
        Warehouse.is_active.is_(True),
    ))
    if not product or not warehouse:
        raise HTTPException(status_code=404, detail="Producto o almacén no encontrado en la empresa activa")
    if product.tracking_mode != mode:
        label = "lotes" if mode == "lot" else "series"
        raise HTTPException(status_code=422, detail=f"El producto no está configurado para control por {label}")
    return product


def _update_weighted_cost(product: Product, total_stock: Decimal, incoming_quantity: Decimal, unit_cost: Decimal) -> None:
    if product.cost_method == "weighted_average":
        product.average_cost = (
            (total_stock * product.average_cost + incoming_quantity * unit_cost)
            / (total_stock + incoming_quantity)
        ).quantize(Decimal("0.01"))


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


@router.get("/valuation")
def inventory_valuation(actor: Actor = Depends(require_permission("inventory:read")), db: Session = Depends(get_db)):
    """Current quantity and accounting-friendly weighted valuation per product."""
    rows = db.execute(select(
        Product.id.label("product_id"), Product.sku, Product.name,
        Product.cost_method, Product.average_cost,
        func.coalesce(func.sum(Inventory.quantity), 0).label("quantity"),
    ).outerjoin(Inventory, (Inventory.product_id == Product.id) & (Inventory.tenant_id == Product.tenant_id)).where(
        Product.tenant_id == actor.tenant_id,
    ).group_by(Product.id, Product.sku, Product.name, Product.cost_method, Product.average_cost).order_by(Product.name)).mappings().all()
    items = [{**row, "inventory_value": (Decimal(str(row["quantity"])) * Decimal(str(row["average_cost"]))).quantize(Decimal("0.01"))} for row in rows]
    return {"valuation": items, "total_value": sum((item["inventory_value"] for item in items), Decimal("0.00"))}


@router.get("/lots")
def list_lots(
    product_id: UUID | None = None,
    warehouse_id: UUID | None = None,
    actor: Actor = Depends(require_permission("inventory:read")),
    db: Session = Depends(get_db),
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=100),
):
    statement = select(InventoryLot).where(InventoryLot.tenant_id == actor.tenant_id)
    if product_id:
        statement = statement.where(InventoryLot.product_id == product_id)
    if warehouse_id:
        statement = statement.where(InventoryLot.warehouse_id == warehouse_id)
    statement = statement.order_by(InventoryLot.expiration_date.asc().nulls_last(), InventoryLot.received_at, InventoryLot.id)
    if not isinstance(page, int) and not isinstance(page_size, int):
        return db.scalars(statement).all()
    return paginate(db, statement, key="lots", page=page or 1, page_size=page_size or 25)


@router.post("/lots/receive", status_code=status.HTTP_201_CREATED)
def receive_lot(data: InventoryLotReceive, actor: Actor = Depends(require_permission("inventory:write")), db: Session = Depends(get_db)):
    product = _tracking_product(data.product_id, data.warehouse_id, "lot", actor, db)
    duplicate = db.scalar(select(InventoryLot.id).where(
        InventoryLot.tenant_id == actor.tenant_id,
        InventoryLot.product_id == data.product_id,
        InventoryLot.warehouse_id == data.warehouse_id,
        InventoryLot.lot_number == data.lot_number.strip(),
    ))
    if duplicate:
        raise HTTPException(status_code=409, detail="Ese lote ya existe para este producto y almacén")
    total_stock = db.scalar(select(func.coalesce(func.sum(Inventory.quantity), 0)).where(
        Inventory.tenant_id == actor.tenant_id,
        Inventory.product_id == data.product_id,
    )) or Decimal("0")
    _update_weighted_cost(product, Decimal(str(total_stock)), data.quantity, data.unit_cost)
    lot = InventoryLot(tenant_id=actor.tenant_id, product_id=data.product_id, warehouse_id=data.warehouse_id,
                       lot_number=data.lot_number.strip(), original_quantity=data.quantity, available_quantity=data.quantity,
                       unit_cost=data.unit_cost, expiration_date=data.expiration_date, created_by=actor.user_id)
    db.add(lot)
    db.flush()
    movement = change_stock(db, tenant_id=actor.tenant_id, product_id=data.product_id, warehouse_id=data.warehouse_id,
                            quantity=data.quantity, movement_type="lot_receipt", reference=f"LOT-{lot.lot_number}",
                            unit_cost=data.unit_cost, note=data.note)
    db.flush()
    db.execute(__import__("sqlalchemy").text("""
        insert into inventory_movement_allocations(tenant_id,inventory_movement_id,lot_id,quantity)
        values(:tenant,:movement,:lot,:quantity)
    """), {"tenant": str(actor.tenant_id), "movement": str(movement.id), "lot": str(lot.id), "quantity": data.quantity})
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="receive_lot", module="inventory", entity="inventory_lot", entity_id=lot.id)
    db.commit()
    db.refresh(lot)
    return lot


@router.get("/serials")
def list_serials(
    product_id: UUID | None = None,
    warehouse_id: UUID | None = None,
    actor: Actor = Depends(require_permission("inventory:read")),
    db: Session = Depends(get_db),
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=100),
):
    statement = select(InventorySerial).where(InventorySerial.tenant_id == actor.tenant_id)
    if product_id:
        statement = statement.where(InventorySerial.product_id == product_id)
    if warehouse_id:
        statement = statement.where(InventorySerial.warehouse_id == warehouse_id)
    statement = statement.order_by(InventorySerial.received_at.desc(), InventorySerial.id)
    if not isinstance(page, int) and not isinstance(page_size, int):
        return db.scalars(statement).all()
    return paginate(db, statement, key="serials", page=page or 1, page_size=page_size or 25)


@router.post("/serials/receive", status_code=status.HTTP_201_CREATED)
def receive_serials(data: InventorySerialReceive, actor: Actor = Depends(require_permission("inventory:write")), db: Session = Depends(get_db)):
    product = _tracking_product(data.product_id, data.warehouse_id, "serial", actor, db)
    existing = set(db.scalars(select(InventorySerial.serial_number).where(
        InventorySerial.tenant_id == actor.tenant_id,
        InventorySerial.product_id == data.product_id,
        InventorySerial.serial_number.in_(data.serial_numbers),
    )).all())
    if existing:
        raise HTTPException(status_code=409, detail=f"Las series ya existen: {', '.join(sorted(existing))}")
    quantity = Decimal(len(data.serial_numbers))
    total_stock = db.scalar(select(func.coalesce(func.sum(Inventory.quantity), 0)).where(
        Inventory.tenant_id == actor.tenant_id,
        Inventory.product_id == data.product_id,
    )) or Decimal("0")
    _update_weighted_cost(product, Decimal(str(total_stock)), quantity, data.unit_cost)
    serials = [InventorySerial(tenant_id=actor.tenant_id, product_id=data.product_id, warehouse_id=data.warehouse_id,
                               serial_number=serial, unit_cost=data.unit_cost, created_by=actor.user_id)
               for serial in data.serial_numbers]
    db.add_all(serials)
    db.flush()
    movement = change_stock(db, tenant_id=actor.tenant_id, product_id=data.product_id, warehouse_id=data.warehouse_id,
                            quantity=quantity, movement_type="serial_receipt", reference=f"SER-{product.sku}",
                            unit_cost=data.unit_cost, note=data.note)
    db.flush()
    for serial in serials:
        db.execute(__import__("sqlalchemy").text("""
            insert into inventory_movement_allocations(tenant_id,inventory_movement_id,serial_id,quantity)
            values(:tenant,:movement,:serial,1)
        """), {"tenant": str(actor.tenant_id), "movement": str(movement.id), "serial": str(serial.id)})
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="receive_serials", module="inventory", entity="inventory_serial", entity_id=serials[0].id, detail=f"{len(serials)} series")
    db.commit()
    return serials


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
