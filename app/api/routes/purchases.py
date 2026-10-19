from datetime import datetime, time, timezone
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import AccountPayable, Product, Purchase, PurchaseItem, Supplier, Warehouse
from app.schemas.transactions import CancelTransaction, PurchaseCreate, PurchaseOut
from app.security.auth import Actor, require_permission
from app.services.audit import record
from app.services.stock import change_stock
from app.services.accounting import post_entry, reverse_entry, split_tax

router = APIRouter(prefix="/purchases", tags=["purchases"])


@router.get("/warehouses")
def list_purchase_warehouses(actor: Actor = Depends(require_permission("purchases:read")), db: Session = Depends(get_db)):
    """Expose active warehouse identities required when receiving a purchase."""
    return db.scalars(select(Warehouse).where(
        Warehouse.tenant_id == actor.tenant_id,
        Warehouse.is_active.is_(True),
    ).order_by(Warehouse.name)).all()


@router.get("", response_model=list[PurchaseOut])
def list_purchases(actor: Actor = Depends(require_permission("purchases:read")), db: Session = Depends(get_db)):
    return db.scalars(select(Purchase).where(Purchase.tenant_id == actor.tenant_id).order_by(Purchase.created_at.desc())).all()


@router.post("", response_model=PurchaseOut, status_code=status.HTTP_201_CREATED)
def receive_purchase(data: PurchaseCreate, actor: Actor = Depends(require_permission("purchases:write")), db: Session = Depends(get_db)):
    supplier = db.scalar(select(Supplier.id).where(Supplier.id == data.supplier_id, Supplier.tenant_id == actor.tenant_id, Supplier.is_active.is_(True)))
    warehouse = db.scalar(select(Warehouse.id).where(Warehouse.id == data.warehouse_id, Warehouse.tenant_id == actor.tenant_id, Warehouse.is_active.is_(True)))
    product_ids = {line.product_id for line in data.lines}
    products = set(db.scalars(select(Product.id).where(Product.tenant_id == actor.tenant_id, Product.id.in_(product_ids), Product.is_active.is_(True))).all())
    if not supplier or not warehouse or products != product_ids:
        raise HTTPException(status_code=404, detail="Proveedor, almacén o producto no pertenece a la empresa activa")
    total = sum((line.quantity * line.unit_cost for line in data.lines), Decimal("0"))
    purchase = Purchase(
        tenant_id=actor.tenant_id, number=data.number, supplier_id=data.supplier_id, warehouse_id=data.warehouse_id,
        status="received", total=total, note=data.note, supplier_document_type=data.supplier_document_type,
        supplier_document_series=data.supplier_document_series, supplier_document_number=data.supplier_document_number,
        supplier_document_date=datetime.combine(data.supplier_document_date, time.min, tzinfo=timezone.utc) if data.supplier_document_date else None,
        supplier_tax_amount=data.supplier_tax_amount,
    )
    db.add(purchase)
    try:
        db.flush()
        db.add(AccountPayable(tenant_id=actor.tenant_id, purchase_id=purchase.id, supplier_id=data.supplier_id, original_amount=total, balance_amount=total, status="open", due_date=data.due_date or datetime.now(timezone.utc).date()))
        for line in data.lines:
            db.add(PurchaseItem(tenant_id=actor.tenant_id, purchase_id=purchase.id, product_id=line.product_id, quantity=line.quantity, unit_cost=line.unit_cost, line_total=line.quantity * line.unit_cost))
            stock_before = db.execute(__import__("sqlalchemy").text("select coalesce(quantity,0) from inventories where tenant_id=:tenant and product_id=:product and warehouse_id=:warehouse for update"), {"tenant": str(actor.tenant_id), "product": str(line.product_id), "warehouse": str(data.warehouse_id)}).scalar() or Decimal("0")
            product_cost = db.scalar(select(Product).where(Product.id == line.product_id, Product.tenant_id == actor.tenant_id).with_for_update())
            if product_cost and product_cost.cost_method == "weighted_average":
                base_value = stock_before * product_cost.average_cost
                product_cost.average_cost = ((base_value + line.quantity * line.unit_cost) / (stock_before + line.quantity)).quantize(Decimal("0.01"))
            change_stock(db, tenant_id=actor.tenant_id, product_id=line.product_id, warehouse_id=data.warehouse_id, quantity=line.quantity, movement_type="receipt", reference=data.number)
        tax = data.supplier_tax_amount if data.supplier_tax_amount is not None else split_tax(total)[1]
        net = total - tax
        post_entry(db, tenant_id=actor.tenant_id, actor_id=actor.user_id, number=f"AS-{data.number}", description=f"Compra {data.number}", source_type="purchase", source_id=purchase.id, lines=[("2010", net, Decimal("0")), ("4011", tax, Decimal("0")), ("4210", Decimal("0"), total)])
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="receive", module="purchases", entity="purchase", entity_id=purchase.id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El número de compra ya existe en esta empresa") from exc
    db.refresh(purchase)
    return purchase


@router.post("/{purchase_id}/cancel", response_model=PurchaseOut)
def cancel_purchase(purchase_id: str, data: CancelTransaction, actor: Actor = Depends(require_permission("purchases:write")), db: Session = Depends(get_db)):
    purchase = db.scalar(select(Purchase).where(Purchase.id == purchase_id, Purchase.tenant_id == actor.tenant_id).with_for_update())
    account = db.scalar(select(AccountPayable).where(AccountPayable.purchase_id == purchase_id, AccountPayable.tenant_id == actor.tenant_id).with_for_update())
    if not purchase or purchase.status != "received" or not account:
        raise HTTPException(status_code=409, detail="La compra no se puede cancelar")
    if account.balance_amount != account.original_amount:
        raise HTTPException(status_code=409, detail="No se puede cancelar una compra con pagos aplicados")
    items = db.scalars(select(PurchaseItem).where(PurchaseItem.purchase_id == purchase.id, PurchaseItem.tenant_id == actor.tenant_id)).all()
    try:
        for item in items:
            change_stock(db, tenant_id=actor.tenant_id, product_id=item.product_id, warehouse_id=purchase.warehouse_id, quantity=-item.quantity, movement_type="issue", reference=f"CANCEL-{purchase.number}")
        purchase.status = "cancelled"
        account.status = "cancelled"
        reverse_entry(db, tenant_id=actor.tenant_id, actor_id=actor.user_id, number=f"REV-{purchase.number}", description=f"Reversión de compra {purchase.number}", original_source_type="purchase", original_source_id=purchase.id, reversal_source_type="purchase_cancel")
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="cancel", module="purchases", entity="purchase", entity_id=purchase.id)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    db.refresh(purchase)
    return purchase
