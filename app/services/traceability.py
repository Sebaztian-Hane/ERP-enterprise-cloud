"""Tenant-scoped lot and serial allocation for inventory movements."""

from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.entities import InventoryLot, InventoryMovement, InventorySerial, Product
from app.services.stock import change_stock


def _allocation(db: Session, *, tenant_id: UUID, movement_id: UUID, lot_id: UUID | None = None,
                serial_id: UUID | None = None, quantity: Decimal = Decimal("1")) -> None:
    db.execute(text("""
        insert into inventory_movement_allocations
          (tenant_id, inventory_movement_id, lot_id, serial_id, quantity)
        values (:tenant, :movement, :lot, :serial, :quantity)
    """), {"tenant": str(tenant_id), "movement": str(movement_id),
            "lot": str(lot_id) if lot_id else None, "serial": str(serial_id) if serial_id else None,
            "quantity": quantity})


def receive_tracked_stock(
    db: Session, *, tenant_id: UUID, actor_id: UUID, product: Product, warehouse_id: UUID,
    quantity: Decimal, unit_cost: Decimal, reference: str, lot_number: str | None = None,
    expiration_date: date | None = None, serial_numbers: list[str] | None = None,
) -> InventoryMovement:
    """Create traceability records and their immutable incoming movement."""
    if product.tracking_mode == "none":
        if lot_number or serial_numbers:
            raise HTTPException(status_code=422, detail="El producto no utiliza control por lote o serie")
        return change_stock(db, tenant_id=tenant_id, product_id=product.id, warehouse_id=warehouse_id,
                            quantity=quantity, movement_type="receipt", reference=reference, unit_cost=unit_cost)
    if product.tracking_mode == "lot":
        lot_code = (lot_number or "").strip()
        if not lot_code:
            raise HTTPException(status_code=422, detail=f"{product.name} requiere un número de lote")
        duplicate = db.scalar(select(InventoryLot.id).where(
            InventoryLot.tenant_id == tenant_id, InventoryLot.product_id == product.id,
            InventoryLot.warehouse_id == warehouse_id, InventoryLot.lot_number == lot_code,
        ))
        if duplicate:
            raise HTTPException(status_code=409, detail=f"El lote {lot_code} ya existe para {product.name} en este almacén")
        lot = InventoryLot(tenant_id=tenant_id, product_id=product.id, warehouse_id=warehouse_id,
                           lot_number=lot_code, original_quantity=quantity, available_quantity=quantity,
                           unit_cost=unit_cost, expiration_date=expiration_date, created_by=actor_id)
        db.add(lot)
        db.flush()
        movement = change_stock(db, tenant_id=tenant_id, product_id=product.id, warehouse_id=warehouse_id,
                                quantity=quantity, movement_type="lot_receipt", reference=reference, unit_cost=unit_cost)
        db.flush()
        _allocation(db, tenant_id=tenant_id, movement_id=movement.id, lot_id=lot.id, quantity=quantity)
        return movement
    serials = [value.strip() for value in (serial_numbers or []) if value.strip()]
    if len(serials) != int(quantity) or len(set(serials)) != len(serials):
        raise HTTPException(status_code=422, detail=f"{product.name} requiere exactamente una serie única por unidad recibida")
    existing = set(db.scalars(select(InventorySerial.serial_number).where(
        InventorySerial.tenant_id == tenant_id, InventorySerial.product_id == product.id,
        InventorySerial.serial_number.in_(serials),
    )).all())
    if existing:
        raise HTTPException(status_code=409, detail=f"Las series ya existen: {', '.join(sorted(existing))}")
    records = [InventorySerial(tenant_id=tenant_id, product_id=product.id, warehouse_id=warehouse_id,
                               serial_number=serial, unit_cost=unit_cost, created_by=actor_id) for serial in serials]
    db.add_all(records)
    db.flush()
    movement = change_stock(db, tenant_id=tenant_id, product_id=product.id, warehouse_id=warehouse_id,
                            quantity=quantity, movement_type="serial_receipt", reference=reference, unit_cost=unit_cost)
    db.flush()
    for serial in records:
        _allocation(db, tenant_id=tenant_id, movement_id=movement.id, serial_id=serial.id)
    return movement


def issue_tracked_stock(
    db: Session, *, tenant_id: UUID, product: Product, warehouse_id: UUID, quantity: Decimal,
    reference: str, serial_numbers: list[str] | None = None,
) -> InventoryMovement:
    """Consume lots FEFO or explicit serials. The stock balance and allocation commit together."""
    if product.tracking_mode == "none":
        return change_stock(db, tenant_id=tenant_id, product_id=product.id, warehouse_id=warehouse_id,
                            quantity=-quantity, movement_type="issue", reference=reference, unit_cost=product.average_cost)
    if product.tracking_mode == "lot":
        lots = db.scalars(select(InventoryLot).where(
            InventoryLot.tenant_id == tenant_id, InventoryLot.product_id == product.id,
            InventoryLot.warehouse_id == warehouse_id, InventoryLot.status == "available",
            InventoryLot.available_quantity > 0,
        ).order_by(InventoryLot.expiration_date.asc().nulls_last(), InventoryLot.received_at, InventoryLot.id).with_for_update()).all()
        remaining = quantity
        allocations: list[tuple[InventoryLot, Decimal]] = []
        for lot in lots:
            take = min(remaining, lot.available_quantity)
            if take > 0:
                allocations.append((lot, take))
                remaining -= take
            if remaining == 0:
                break
        if remaining > 0:
            raise HTTPException(status_code=422, detail=f"No hay lotes disponibles suficientes para {product.name}")
        total_cost = sum((lot.unit_cost * amount for lot, amount in allocations), Decimal("0"))
        movement = change_stock(db, tenant_id=tenant_id, product_id=product.id, warehouse_id=warehouse_id,
                                quantity=-quantity, movement_type="lot_issue", reference=reference,
                                unit_cost=(total_cost / quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        db.flush()
        for lot, amount in allocations:
            lot.available_quantity -= amount
            if lot.available_quantity == 0:
                lot.status = "exhausted"
            _allocation(db, tenant_id=tenant_id, movement_id=movement.id, lot_id=lot.id, quantity=amount)
        return movement
    serials = [value.strip() for value in (serial_numbers or []) if value.strip()]
    if len(serials) != int(quantity) or len(set(serials)) != len(serials):
        raise HTTPException(status_code=422, detail=f"{product.name} requiere las series exactas de las unidades vendidas")
    records = db.scalars(select(InventorySerial).where(
        InventorySerial.tenant_id == tenant_id, InventorySerial.product_id == product.id,
        InventorySerial.warehouse_id == warehouse_id, InventorySerial.serial_number.in_(serials),
    ).with_for_update()).all()
    if len(records) != len(serials) or {item.serial_number for item in records} != set(serials) or any(item.status != "available" for item in records):
        raise HTTPException(status_code=422, detail="Una o más series no están disponibles en el almacén seleccionado")
    average = (sum((item.unit_cost for item in records), Decimal("0")) / quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    movement = change_stock(db, tenant_id=tenant_id, product_id=product.id, warehouse_id=warehouse_id,
                            quantity=-quantity, movement_type="serial_issue", reference=reference, unit_cost=average)
    db.flush()
    for serial in records:
        serial.status = "issued"
        serial.issued_at = datetime.now(timezone.utc)
        _allocation(db, tenant_id=tenant_id, movement_id=movement.id, serial_id=serial.id)
    return movement


def return_tracked_sale_stock(
    db: Session, *, tenant_id: UUID, product: Product, warehouse_id: UUID, quantity: Decimal,
    sale_reference: str, reversal_reference: str,
) -> InventoryMovement:
    """Return exactly the lots or serials consumed by a sale cancellation."""
    outgoing = db.scalars(select(InventoryMovement).where(
        InventoryMovement.tenant_id == tenant_id, InventoryMovement.product_id == product.id,
        InventoryMovement.warehouse_id == warehouse_id, InventoryMovement.reference == sale_reference,
        InventoryMovement.movement_type.in_(("lot_issue", "serial_issue")),
    ).with_for_update()).all()
    if product.tracking_mode == "none":
        return change_stock(db, tenant_id=tenant_id, product_id=product.id, warehouse_id=warehouse_id,
                            quantity=quantity, movement_type="receipt", reference=reversal_reference,
                            unit_cost=product.average_cost)
    if not outgoing:
        raise HTTPException(status_code=409, detail="No se encontró la trazabilidad original de esta venta")
    if product.tracking_mode == "lot":
        allocations = db.execute(text("""
          select a.lot_id, a.quantity from inventory_movement_allocations a
          join inventory_movements m on m.id=a.inventory_movement_id
          where a.tenant_id=:tenant and m.tenant_id=:tenant and m.product_id=:product
            and m.warehouse_id=:warehouse and m.reference=:reference and m.movement_type='lot_issue'
          for update of a
        """), {"tenant": str(tenant_id), "product": str(product.id), "warehouse": str(warehouse_id),
               "reference": sale_reference}).mappings().all()
        if sum((Decimal(str(row["quantity"])) for row in allocations), Decimal("0")) != quantity:
            raise HTTPException(status_code=409, detail="La trazabilidad de lotes de la venta no coincide con sus líneas")
        lots = {lot.id: lot for lot in db.scalars(select(InventoryLot).where(
            InventoryLot.id.in_([row["lot_id"] for row in allocations]), InventoryLot.tenant_id == tenant_id,
        ).with_for_update()).all()}
        total_cost = Decimal("0")
        for row in allocations:
            lot = lots.get(row["lot_id"])
            if not lot:
                raise HTTPException(status_code=409, detail="Un lote original ya no está disponible para la devolución")
            amount = Decimal(str(row["quantity"]))
            lot.available_quantity += amount
            lot.status = "available"
            total_cost += lot.unit_cost * amount
        movement = change_stock(db, tenant_id=tenant_id, product_id=product.id, warehouse_id=warehouse_id,
                                quantity=quantity, movement_type="lot_return", reference=reversal_reference,
                                unit_cost=(total_cost / quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        db.flush()
        for row in allocations:
            _allocation(db, tenant_id=tenant_id, movement_id=movement.id, lot_id=row["lot_id"], quantity=Decimal(str(row["quantity"])))
        return movement
    allocations = db.execute(text("""
      select a.serial_id from inventory_movement_allocations a
      join inventory_movements m on m.id=a.inventory_movement_id
      where a.tenant_id=:tenant and m.tenant_id=:tenant and m.product_id=:product
        and m.warehouse_id=:warehouse and m.reference=:reference and m.movement_type='serial_issue'
      for update of a
    """), {"tenant": str(tenant_id), "product": str(product.id), "warehouse": str(warehouse_id),
           "reference": sale_reference}).mappings().all()
    if len(allocations) != int(quantity):
        raise HTTPException(status_code=409, detail="La trazabilidad de series de la venta no coincide con sus líneas")
    records = db.scalars(select(InventorySerial).where(
        InventorySerial.id.in_([row["serial_id"] for row in allocations]), InventorySerial.tenant_id == tenant_id,
    ).with_for_update()).all()
    if len(records) != len(allocations) or any(item.status != "issued" for item in records):
        raise HTTPException(status_code=409, detail="Una serie de la venta no puede devolverse en su estado actual")
    average = (sum((item.unit_cost for item in records), Decimal("0")) / quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    for serial in records:
        serial.status = "available"
        serial.issued_at = None
    movement = change_stock(db, tenant_id=tenant_id, product_id=product.id, warehouse_id=warehouse_id,
                            quantity=quantity, movement_type="serial_return", reference=reversal_reference, unit_cost=average)
    db.flush()
    for row in allocations:
        _allocation(db, tenant_id=tenant_id, movement_id=movement.id, serial_id=row["serial_id"])
    return movement


def reverse_tracked_purchase_receipt(
    db: Session, *, tenant_id: UUID, product: Product, warehouse_id: UUID, quantity: Decimal,
    purchase_reference: str, reversal_reference: str,
) -> InventoryMovement:
    """Reverse an incoming tracked receipt only while its exact evidence remains unused."""
    if product.tracking_mode == "none":
        return change_stock(db, tenant_id=tenant_id, product_id=product.id, warehouse_id=warehouse_id,
                            quantity=-quantity, movement_type="issue", reference=reversal_reference,
                            unit_cost=product.average_cost)
    movement_kind = "lot_receipt" if product.tracking_mode == "lot" else "serial_receipt"
    if product.tracking_mode == "lot":
        allocations = db.execute(text("""
          select a.lot_id, a.quantity from inventory_movement_allocations a
          join inventory_movements m on m.id=a.inventory_movement_id
          where a.tenant_id=:tenant and m.tenant_id=:tenant and m.product_id=:product
            and m.warehouse_id=:warehouse and m.reference=:reference and m.movement_type=:kind
          for update of a
        """), {"tenant": str(tenant_id), "product": str(product.id), "warehouse": str(warehouse_id),
               "reference": purchase_reference, "kind": movement_kind}).mappings().all()
        if sum((Decimal(str(row["quantity"])) for row in allocations), Decimal("0")) != quantity:
            raise HTTPException(status_code=409, detail="La compra no conserva una trazabilidad de lotes reversible")
        lots = {lot.id: lot for lot in db.scalars(select(InventoryLot).where(
            InventoryLot.id.in_([row["lot_id"] for row in allocations]), InventoryLot.tenant_id == tenant_id,
        ).with_for_update()).all()}
        total_cost = Decimal("0")
        for row in allocations:
            lot = lots.get(row["lot_id"])
            amount = Decimal(str(row["quantity"]))
            if not lot or lot.status != "available" or lot.available_quantity < amount:
                raise HTTPException(status_code=409, detail="No se puede cancelar la compra: uno de sus lotes ya fue consumido o bloqueado")
            lot.available_quantity -= amount
            lot.status = "blocked"
            total_cost += lot.unit_cost * amount
        movement = change_stock(db, tenant_id=tenant_id, product_id=product.id, warehouse_id=warehouse_id,
                                quantity=-quantity, movement_type="lot_receipt_reversal", reference=reversal_reference,
                                unit_cost=(total_cost / quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        db.flush()
        for row in allocations:
            _allocation(db, tenant_id=tenant_id, movement_id=movement.id, lot_id=row["lot_id"], quantity=Decimal(str(row["quantity"])))
        return movement
    allocations = db.execute(text("""
      select a.serial_id from inventory_movement_allocations a
      join inventory_movements m on m.id=a.inventory_movement_id
      where a.tenant_id=:tenant and m.tenant_id=:tenant and m.product_id=:product
        and m.warehouse_id=:warehouse and m.reference=:reference and m.movement_type=:kind
      for update of a
    """), {"tenant": str(tenant_id), "product": str(product.id), "warehouse": str(warehouse_id),
           "reference": purchase_reference, "kind": movement_kind}).mappings().all()
    if len(allocations) != int(quantity):
        raise HTTPException(status_code=409, detail="La compra no conserva una trazabilidad de series reversible")
    records = db.scalars(select(InventorySerial).where(
        InventorySerial.id.in_([row["serial_id"] for row in allocations]), InventorySerial.tenant_id == tenant_id,
    ).with_for_update()).all()
    if len(records) != len(allocations) or any(item.status != "available" for item in records):
        raise HTTPException(status_code=409, detail="No se puede cancelar la compra: una serie ya fue consumida o bloqueada")
    average = (sum((item.unit_cost for item in records), Decimal("0")) / quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    for serial in records:
        serial.status = "blocked"
    movement = change_stock(db, tenant_id=tenant_id, product_id=product.id, warehouse_id=warehouse_id,
                            quantity=-quantity, movement_type="serial_receipt_reversal", reference=reversal_reference, unit_cost=average)
    db.flush()
    for row in allocations:
        _allocation(db, tenant_id=tenant_id, movement_id=movement.id, serial_id=row["serial_id"])
    return movement
