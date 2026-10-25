from decimal import Decimal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import Branch, Inventory, InventoryLot, InventoryMovement, InventorySerial, InventoryTransfer, Product, Warehouse
from app.schemas.inventory import InventoryLotReceive, InventoryMovementOut, InventoryOut, InventorySerialReceive, InventoryTransferCreate, InventoryTransferOut, StockAdjustment, WarehouseCreate, WarehouseOut, WarehouseUpdate
from app.schemas.pagination import paginate
from app.security.auth import Actor, require_permission, require_second_factor
from app.services.audit import record
from app.services.approval_documents import create_inventory_adjustment_evidence
from app.services.stock import change_stock
from app.services.idempotency import claim as claim_idempotency, complete as complete_idempotency, replay_response
from app.services.traceability import issue_tracked_stock

router = APIRouter(prefix="/inventory", tags=["inventory"])


def _ensure_branch_in_tenant(branch_id: UUID | None, actor: Actor, db: Session) -> None:
    """Prevent a warehouse from linking to a branch in another tenant."""
    if branch_id is None:
        return
    branch = db.scalar(select(Branch.id).where(
        Branch.id == branch_id,
        Branch.tenant_id == actor.tenant_id,
        Branch.is_active.is_(True),
    ))
    if not branch:
        raise HTTPException(status_code=404, detail="La sucursal no existe o no pertenece a la empresa activa")


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
    _ensure_branch_in_tenant(data.branch_id, actor, db)
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
    values = data.model_dump(exclude_unset=True)
    if "branch_id" in values:
        _ensure_branch_in_tenant(values["branch_id"], actor, db)
    for field, value in values.items():
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


@router.get("/reconciliation")
def inventory_reconciliation(
    product_id: UUID | None = None,
    warehouse_id: UUID | None = None,
    actor: Actor = Depends(require_permission("inventory:read")),
    db: Session = Depends(get_db),
):
    """Compare the materialized stock balance against the immutable kardex.

    This is deliberately diagnostic.  A historical import can legitimately
    start with an opening balance, so silently overwriting ``inventories`` from
    movements would turn an audit warning into lost stock.  Differences must be
    regularised through the already approved physical-count workflow.
    """
    filters = ["i.tenant_id = :tenant"]
    values: dict[str, str] = {"tenant": str(actor.tenant_id)}
    if product_id:
        filters.append("i.product_id = :product")
        values["product"] = str(product_id)
    if warehouse_id:
        filters.append("i.warehouse_id = :warehouse")
        values["warehouse"] = str(warehouse_id)
    rows = db.execute(text(f"""
        select
          i.product_id, p.sku, p.name as product_name,
          i.warehouse_id, w.code as warehouse_code, w.name as warehouse_name,
          i.quantity as on_hand_quantity,
          coalesce(sum(m.quantity), 0) as kardex_quantity,
          i.quantity - coalesce(sum(m.quantity), 0) as difference_quantity,
          count(m.id)::integer as movement_count,
          max(m.created_at) as last_movement_at
        from inventories i
        join products p on p.id=i.product_id and p.tenant_id=i.tenant_id
        join warehouses w on w.id=i.warehouse_id and w.tenant_id=i.tenant_id
        left join inventory_movements m on m.tenant_id=i.tenant_id
          and m.product_id=i.product_id and m.warehouse_id=i.warehouse_id
        where {' and '.join(filters)}
        group by i.product_id, p.sku, p.name, i.warehouse_id, w.code, w.name, i.quantity
        order by p.name, w.name
    """), values).mappings().all()
    items = []
    for row in rows:
        difference = Decimal(str(row["difference_quantity"]))
        movements = int(row["movement_count"])
        # A balance without movement history is an opening balance/import, not
        # necessarily an error. It remains visible for a controlled count.
        state = "matched" if difference == 0 else ("opening_balance" if movements == 0 else "requires_physical_count")
        items.append({**dict(row), "status": state})
    return {
        "reconciliation": items,
        "summary": {
            "total": len(items),
            "matched": sum(item["status"] == "matched" for item in items),
            "opening_balances": sum(item["status"] == "opening_balance" for item in items),
            "requires_physical_count": sum(item["status"] == "requires_physical_count" for item in items),
        },
    }


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


@router.post("/adjustments", status_code=status.HTTP_201_CREATED)
def adjust_stock(request: Request, data: StockAdjustment, actor: Actor = Depends(require_permission("inventory:write")), db: Session = Depends(get_db)):
    idempotency = claim_idempotency(
        db,
        tenant_id=actor.tenant_id,
        actor_user_id=actor.user_id,
        operation="inventory.adjustments.create",
        key=request.headers.get("Idempotency-Key"),
        payload=data.model_dump(mode="json"),
    )
    if idempotency and idempotency.is_replay:
        return replay_response(idempotency)
    product = db.scalar(select(Product).where(Product.id == data.product_id, Product.tenant_id == actor.tenant_id, Product.is_active.is_(True)))
    warehouse = db.scalar(select(Warehouse).where(Warehouse.id == data.warehouse_id, Warehouse.tenant_id == actor.tenant_id, Warehouse.is_active.is_(True)))
    if not product or not warehouse:
        raise HTTPException(status_code=404, detail="Producto o almacén no encontrado en la empresa activa")
    row = db.execute(text("""insert into inventory_adjustment_requests(tenant_id,product_id,warehouse_id,quantity,reference,reason,note,requested_by)
      values(:tenant,:product,:warehouse,:quantity,:reference,:reason,:note,:actor) returning *"""), {"tenant":str(actor.tenant_id),"product":str(data.product_id),"warehouse":str(data.warehouse_id),"quantity":data.quantity,"reference":data.reference,"reason":data.reason,"note":data.note,"actor":str(actor.user_id)}).mappings().one()
    policy = db.execute(text("""select id from approval_policies where tenant_id=:tenant and module='inventory' and action='stock_adjustment' and is_active=true order by min_amount limit 1"""), {"tenant":str(actor.tenant_id)}).scalar()
    approval_id = db.execute(text("""insert into approval_requests(tenant_id,policy_id,module,action,entity_type,entity_id,payload,requested_by)
      values(:tenant,:policy,'inventory','stock_adjustment','inventory_adjustment',:id,'{}'::jsonb,:actor) returning id"""), {"tenant":str(actor.tenant_id),"policy":str(policy) if policy else None,"id":str(row["id"]),"actor":str(actor.user_id)}).scalar_one()
    db.execute(text("update inventory_adjustment_requests set approval_request_id=:approval where id=:id"),{"approval":str(approval_id),"id":str(row["id"])})
    create_inventory_adjustment_evidence(db, tenant_id=actor.tenant_id, adjustment_id=row["id"], approval_request_id=approval_id, requested_by=actor.user_id)
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="submit_adjustment", module="inventory", entity="inventory_adjustment", entity_id=row["id"])
    complete_idempotency(
        db,
        claim=idempotency,
        response_body={"id": str(row["id"]), "status": "submitted", "approval_request_id": str(approval_id)},
        response_status=status.HTTP_201_CREATED,
    )
    db.commit()
    return {"id": row["id"], "status": "submitted", "approval_request_id": approval_id}


@router.post("/adjustments/{adjustment_id}/execute", response_model=InventoryMovementOut)
def execute_adjustment(adjustment_id: UUID, actor: Actor = Depends(require_permission("inventory:write")), _: Actor = Depends(require_second_factor), db: Session = Depends(get_db)):
    adjustment = db.execute(text("""
        select * from inventory_adjustment_requests
        where id=:id and tenant_id=:tenant and status='approved' for update
    """), {"id": str(adjustment_id), "tenant": str(actor.tenant_id)}).mappings().first()
    if not adjustment:
        raise HTTPException(status_code=409, detail="El ajuste debe estar aprobado antes de ejecutarse")
    if str(actor.user_id) in {str(adjustment["requested_by"]), str(adjustment["approved_by"])}:
        raise HTTPException(status_code=403, detail="La separación de funciones exige otro ejecutor")
    stock = db.scalar(select(Inventory).where(
        Inventory.tenant_id == actor.tenant_id,
        Inventory.product_id == adjustment["product_id"],
        Inventory.warehouse_id == adjustment["warehouse_id"],
    ).with_for_update())
    try:
        if stock is None:
            if adjustment["quantity"] < 0:
                raise HTTPException(status_code=422, detail="El ajuste dejaría la existencia en negativo")
            stock = Inventory(tenant_id=actor.tenant_id, product_id=adjustment["product_id"], warehouse_id=adjustment["warehouse_id"], quantity=Decimal("0"))
            db.add(stock)
        if stock.quantity + adjustment["quantity"] < 0:
            raise HTTPException(status_code=422, detail="El ajuste dejaría la existencia en negativo")
        stock.quantity += adjustment["quantity"]
        movement = InventoryMovement(tenant_id=actor.tenant_id, product_id=adjustment["product_id"], warehouse_id=adjustment["warehouse_id"], movement_type="adjustment", quantity=adjustment["quantity"], reference=adjustment["reference"], note=f"{adjustment['reason']}. {adjustment['note'] or ''}".strip())
        db.add(movement); db.flush()
        db.execute(text("""update inventory_adjustment_requests set status='executed',executed_by=:actor,executed_at=now(),inventory_movement_id=:movement,updated_at=now()
            where id=:id and tenant_id=:tenant and status='approved'"""), {"actor":str(actor.user_id),"movement":str(movement.id),"id":str(adjustment_id),"tenant":str(actor.tenant_id)})
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="execute_adjustment", module="inventory", entity="inventory_adjustment", entity_id=adjustment_id)
        db.commit()
    except HTTPException:
        db.rollback(); raise
    db.refresh(movement)
    return movement


@router.post("/transfers", response_model=InventoryTransferOut, status_code=status.HTTP_201_CREATED)
def transfer_stock(request: Request, data: InventoryTransferCreate, actor: Actor = Depends(require_permission("inventory:write")), db: Session = Depends(get_db)):
    idempotency = claim_idempotency(
        db,
        tenant_id=actor.tenant_id,
        actor_user_id=actor.user_id,
        operation="inventory.transfers.create",
        key=request.headers.get("Idempotency-Key"),
        payload=data.model_dump(mode="json"),
    )
    if idempotency and idempotency.is_replay:
        return replay_response(idempotency)
    product = db.scalar(select(Product).where(Product.id == data.product_id, Product.tenant_id == actor.tenant_id, Product.is_active.is_(True)))
    warehouses = set(db.scalars(select(Warehouse.id).where(Warehouse.id.in_({data.source_warehouse_id, data.destination_warehouse_id}), Warehouse.tenant_id == actor.tenant_id, Warehouse.is_active.is_(True))).all())
    if not product or warehouses != {data.source_warehouse_id, data.destination_warehouse_id}:
        raise HTTPException(status_code=404, detail="Producto o almacén no encontrado en la empresa activa")
    if product.tracking_mode == "serial" and not data.serial_numbers:
        raise HTTPException(status_code=422, detail="Un producto serializado requiere las series exactas desde el borrador de transferencia")
    if product.tracking_mode != "serial" and data.serial_numbers:
        raise HTTPException(status_code=422, detail="Solo los productos serializados admiten series en una transferencia")
    # Creation only records the intention.  Stock leaves the source warehouse
    # at dispatch and reaches the destination at receipt, preserving transit.
    transfer = InventoryTransfer(tenant_id=actor.tenant_id, status="draft", **data.model_dump())
    db.add(transfer)
    try:
        db.flush()
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="create_transfer", module="inventory", entity="inventory_transfer", entity_id=transfer.id)
        complete_idempotency(
            db,
            claim=idempotency,
            response_body=InventoryTransferOut.model_validate(transfer).model_dump(mode="json"),
            response_status=status.HTTP_201_CREATED,
        )
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
def dispatch_transfer(transfer_id: UUID, request: Request, actor: Actor = Depends(require_permission("inventory:write")), db: Session = Depends(get_db)):
    idempotency = claim_idempotency(
        db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id,
        operation="inventory.transfers.dispatch", key=request.headers.get("Idempotency-Key"),
        payload={"transfer_id": str(transfer_id)},
    )
    if idempotency and idempotency.is_replay:
        return replay_response(idempotency)
    transfer = db.scalar(select(InventoryTransfer).where(
        InventoryTransfer.id == transfer_id,
        InventoryTransfer.tenant_id == actor.tenant_id,
    ).with_for_update())
    if not transfer or transfer.status != "draft":
        raise HTTPException(status_code=409, detail="La transferencia debe estar en borrador para despacharse")
    try:
        product = db.scalar(select(Product).where(
            Product.id == transfer.product_id, Product.tenant_id == actor.tenant_id,
        ).with_for_update())
        if not product:
            raise HTTPException(status_code=409, detail="El producto de la transferencia ya no existe")
        # Transfers must move the tracked evidence as well as the numerical
        # balance. Lots are allocated FEFO; serialised products require the
        # exact serial list stored on the draft transfer.
        if product.tracking_mode == "serial" and not transfer.serial_numbers:
            raise HTTPException(status_code=422, detail="Una transferencia de producto serializado requiere las series exactas")
        if product.tracking_mode == "none":
            change_stock(db, tenant_id=actor.tenant_id, product_id=transfer.product_id, warehouse_id=transfer.source_warehouse_id,
                         quantity=-transfer.quantity, movement_type="transfer_out", reference=transfer.number)
        else:
            issue_tracked_stock(
                db, tenant_id=actor.tenant_id, product=product, warehouse_id=transfer.source_warehouse_id,
                quantity=transfer.quantity, reference=transfer.number, serial_numbers=transfer.serial_numbers,
            )
        transfer.status = "in_transit"
        db.execute(__import__("sqlalchemy").text("update inventory_transfers set dispatched_by=:actor, dispatched_at=now() where id=:id and tenant_id=:tenant"), {"actor": str(actor.user_id), "id": str(transfer.id), "tenant": str(actor.tenant_id)})
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="dispatch_transfer", module="inventory", entity="inventory_transfer", entity_id=transfer.id)
        complete_idempotency(db, claim=idempotency, response_body=InventoryTransferOut.model_validate(transfer).model_dump(mode="json"), response_status=status.HTTP_200_OK)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    db.refresh(transfer)
    return transfer


@router.post("/transfers/{transfer_id}/receive", response_model=InventoryTransferOut)
def receive_transfer(transfer_id: UUID, request: Request, actor: Actor = Depends(require_permission("inventory:write")), db: Session = Depends(get_db)):
    idempotency = claim_idempotency(
        db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id,
        operation="inventory.transfers.receive", key=request.headers.get("Idempotency-Key"),
        payload={"transfer_id": str(transfer_id)},
    )
    if idempotency and idempotency.is_replay:
        return replay_response(idempotency)
    transfer = db.scalar(select(InventoryTransfer).where(
        InventoryTransfer.id == transfer_id,
        InventoryTransfer.tenant_id == actor.tenant_id,
    ).with_for_update())
    if not transfer or transfer.status != "in_transit":
        raise HTTPException(status_code=409, detail="La transferencia debe estar en tránsito para recibirse")
    product = db.scalar(select(Product).where(
        Product.id == transfer.product_id, Product.tenant_id == actor.tenant_id,
    ).with_for_update())
    if not product:
        raise HTTPException(status_code=409, detail="El producto de la transferencia ya no existe")
    try:
        # Validate all traceability evidence before increasing the destination
        # balance. One transaction then commits the inventory quantity and its
        # lot/serial audit trail atomically.
        lot_allocations = []
        serial_allocations = []
        if product.tracking_mode == "lot":
            lot_allocations = db.execute(text("""
                select a.lot_id, a.quantity from inventory_movement_allocations a
                join inventory_movements m on m.id=a.inventory_movement_id and m.tenant_id=a.tenant_id
                where a.tenant_id=:tenant and m.product_id=:product and m.warehouse_id=:source
                  and m.reference=:reference and m.movement_type='lot_issue'
                for update of a
            """), {"tenant": str(actor.tenant_id), "product": str(product.id), "source": str(transfer.source_warehouse_id), "reference": transfer.number}).mappings().all()
            if sum((Decimal(str(row["quantity"])) for row in lot_allocations), Decimal("0")) != transfer.quantity:
                raise HTTPException(status_code=409, detail="No se encontró una trazabilidad de lotes completa para la transferencia")
        elif product.tracking_mode == "serial":
            serial_allocations = db.execute(text("""
                select a.serial_id from inventory_movement_allocations a
                join inventory_movements m on m.id=a.inventory_movement_id and m.tenant_id=a.tenant_id
                where a.tenant_id=:tenant and m.product_id=:product and m.warehouse_id=:source
                  and m.reference=:reference and m.movement_type='serial_issue'
                for update of a
            """), {"tenant": str(actor.tenant_id), "product": str(product.id), "source": str(transfer.source_warehouse_id), "reference": transfer.number}).mappings().all()
            if len(serial_allocations) != int(transfer.quantity):
                raise HTTPException(status_code=409, detail="No se encontró una trazabilidad de series completa para la transferencia")

        movement = change_stock(db, tenant_id=actor.tenant_id, product_id=transfer.product_id, warehouse_id=transfer.destination_warehouse_id,
                                quantity=transfer.quantity, movement_type="transfer_in", reference=transfer.number, unit_cost=product.average_cost)
        db.flush()
        if product.tracking_mode == "lot":
            source_lots = {lot.id: lot for lot in db.scalars(select(InventoryLot).where(
                InventoryLot.tenant_id == actor.tenant_id,
                InventoryLot.id.in_([row["lot_id"] for row in lot_allocations]),
            ).with_for_update()).all()}
            for row in lot_allocations:
                source_lot = source_lots.get(row["lot_id"])
                if not source_lot:
                    raise HTTPException(status_code=409, detail="No se encontró un lote de la transferencia")
                destination_lot = db.scalar(select(InventoryLot).where(
                    InventoryLot.tenant_id == actor.tenant_id, InventoryLot.product_id == product.id,
                    InventoryLot.warehouse_id == transfer.destination_warehouse_id,
                    InventoryLot.lot_number == source_lot.lot_number,
                ).with_for_update())
                moved = Decimal(str(row["quantity"]))
                if destination_lot:
                    if destination_lot.unit_cost != source_lot.unit_cost or destination_lot.expiration_date != source_lot.expiration_date:
                        raise HTTPException(status_code=409, detail="El lote ya existe en destino con costo o vencimiento distinto")
                    destination_lot.available_quantity += moved
                    destination_lot.original_quantity += moved
                    destination_lot.status = "available"
                else:
                    destination_lot = InventoryLot(tenant_id=actor.tenant_id, product_id=product.id, warehouse_id=transfer.destination_warehouse_id,
                        lot_number=source_lot.lot_number, original_quantity=moved, available_quantity=moved,
                        unit_cost=source_lot.unit_cost, expiration_date=source_lot.expiration_date, status="available", created_by=actor.user_id)
                    db.add(destination_lot)
                    db.flush()
                db.execute(text("""
                    insert into inventory_movement_allocations(tenant_id, inventory_movement_id, lot_id, quantity)
                    values(:tenant, :movement, :lot, :quantity)
                """), {"tenant": str(actor.tenant_id), "movement": str(movement.id), "lot": str(destination_lot.id), "quantity": moved})
        elif product.tracking_mode == "serial":
            records = db.scalars(select(InventorySerial).where(
                InventorySerial.tenant_id == actor.tenant_id,
                InventorySerial.id.in_([row["serial_id"] for row in serial_allocations]),
            ).with_for_update()).all()
            if len(records) != len(serial_allocations) or any(record.status != "issued" for record in records):
                raise HTTPException(status_code=409, detail="Una serie de la transferencia no está disponible para recepción")
            for record in records:
                record.warehouse_id = transfer.destination_warehouse_id
                record.status = "available"
                record.issued_at = None
                db.execute(text("""
                    insert into inventory_movement_allocations(tenant_id, inventory_movement_id, serial_id, quantity)
                    values(:tenant, :movement, :serial, 1)
                """), {"tenant": str(actor.tenant_id), "movement": str(movement.id), "serial": str(record.id)})
        transfer.status = "completed"
        db.execute(__import__("sqlalchemy").text("update inventory_transfers set received_by=:actor, received_at=now() where id=:id and tenant_id=:tenant"), {"actor": str(actor.user_id), "id": str(transfer.id), "tenant": str(actor.tenant_id)})
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="receive_transfer", module="inventory", entity="inventory_transfer", entity_id=transfer.id)
        complete_idempotency(db, claim=idempotency, response_body=InventoryTransferOut.model_validate(transfer).model_dump(mode="json"), response_status=status.HTTP_200_OK)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    db.refresh(transfer)
    return transfer
