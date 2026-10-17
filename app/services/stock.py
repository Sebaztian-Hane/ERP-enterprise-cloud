from decimal import Decimal
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.entities import Inventory, InventoryMovement


def change_stock(db: Session, *, tenant_id: UUID, product_id: UUID, warehouse_id: UUID, quantity: Decimal, movement_type: str, reference: str) -> InventoryMovement:
    stock = db.scalar(select(Inventory).where(Inventory.tenant_id == tenant_id, Inventory.product_id == product_id, Inventory.warehouse_id == warehouse_id).with_for_update())
    if stock is None:
        stock = Inventory(tenant_id=tenant_id, product_id=product_id, warehouse_id=warehouse_id, quantity=Decimal("0"))
        db.add(stock)
    if stock.quantity + quantity < 0:
        raise HTTPException(status_code=422, detail="Existencias insuficientes para completar la venta")
    stock.quantity += quantity
    movement = InventoryMovement(tenant_id=tenant_id, product_id=product_id, warehouse_id=warehouse_id, movement_type=movement_type, quantity=quantity, reference=reference)
    db.add(movement)
    return movement
