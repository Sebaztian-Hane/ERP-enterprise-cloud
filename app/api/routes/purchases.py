from datetime import datetime, time, timezone
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import AccountPayable, Product, Purchase, PurchaseItem, Supplier, Warehouse
from app.schemas.transactions import CancelTransaction, PurchaseCreate, PurchaseOut
from app.schemas.pagination import paginate
from app.security.auth import Actor, require_permission
from app.services.audit import record
from app.services.traceability import receive_tracked_stock, reverse_tracked_purchase_receipt
from app.services.accounting import post_entry, reverse_entry, split_tax
from app.services.idempotency import claim as claim_idempotency, complete as complete_idempotency, replay_response
from app.services.operational_context import resolve_warehouse_context
from app.services.document_sequences import allocate_document_number
from app.services.payment_terms import resolve_payment_term
from app.services.taxes import resolve_default_tax_rate

router = APIRouter(prefix="/purchases", tags=["purchases"])


@router.get("/warehouses")
def list_purchase_warehouses(actor: Actor = Depends(require_permission("purchases:read")), db: Session = Depends(get_db)):
    """Expose active warehouse identities required when receiving a purchase."""
    return db.scalars(select(Warehouse).where(
        Warehouse.tenant_id == actor.tenant_id,
        Warehouse.is_active.is_(True),
    ).order_by(Warehouse.name)).all()


@router.get("/payment-terms")
def list_purchase_payment_terms(actor: Actor = Depends(require_permission("purchases:read")), db: Session = Depends(get_db)):
    """Expose active tenant-owned terms without granting configuration access."""
    return db.execute(text("""
        select id, code, name, days_due
        from payment_terms
        where tenant_id=:tenant and is_active=true
        order by days_due, code, name
    """), {"tenant": str(actor.tenant_id)}).mappings().all()


@router.get("")
def list_purchases(
    actor: Actor = Depends(require_permission("purchases:read")),
    db: Session = Depends(get_db),
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=100),
):
    statement = select(Purchase).where(Purchase.tenant_id == actor.tenant_id).order_by(Purchase.created_at.desc(), Purchase.id)
    if not isinstance(page, int) and not isinstance(page_size, int):
        return db.scalars(statement).all()
    return paginate(db, statement, key="purchases", page=page or 1, page_size=page_size or 25)


@router.post("", response_model=PurchaseOut, status_code=status.HTTP_201_CREATED)
def receive_purchase(request: Request, data: PurchaseCreate, actor: Actor = Depends(require_permission("purchases:write")), db: Session = Depends(get_db)):
    idempotency = claim_idempotency(
        db,
        tenant_id=actor.tenant_id,
        actor_user_id=actor.user_id,
        operation="purchases.receive",
        key=request.headers.get("Idempotency-Key"),
        payload=data.model_dump(mode="json"),
    )
    if idempotency and idempotency.is_replay:
        return replay_response(idempotency)
    supplier = db.scalar(select(Supplier.id).where(Supplier.id == data.supplier_id, Supplier.tenant_id == actor.tenant_id, Supplier.is_active.is_(True)))
    operational_context = resolve_warehouse_context(
        db, tenant_id=actor.tenant_id, warehouse_id=data.warehouse_id,
    )
    product_ids = {line.product_id for line in data.lines}
    products = set(db.scalars(select(Product.id).where(Product.tenant_id == actor.tenant_id, Product.id.in_(product_ids), Product.is_active.is_(True))).all())
    if not supplier or products != product_ids:
        raise HTTPException(status_code=404, detail="Proveedor, almacén o producto no pertenece a la empresa activa")
    total = sum((line.quantity * line.unit_cost for line in data.lines), Decimal("0"))
    document_date = data.supplier_document_date or datetime.now(timezone.utc).date()
    tax_rate = resolve_default_tax_rate(
        db, tenant_id=actor.tenant_id, document_date=document_date,
    )
    payment_term, due_date = resolve_payment_term(
        db,
        tenant_id=actor.tenant_id,
        payment_term_id=data.payment_term_id,
        supplied_due_date=data.due_date,
        document_date=document_date,
    )
    number = allocate_document_number(
        db, tenant_id=actor.tenant_id, module="purchases", document_type="purchase",
        legal_entity_id=operational_context.legal_entity.id if operational_context.legal_entity else None,
        branch_id=operational_context.branch.id if operational_context.branch else None,
    )
    purchase = Purchase(
        tenant_id=actor.tenant_id, number=number, supplier_id=data.supplier_id, warehouse_id=data.warehouse_id,
        legal_entity_id=operational_context.legal_entity.id if operational_context.legal_entity else None,
        branch_id=operational_context.branch.id if operational_context.branch else None,
        status="received", total=total, note=data.note, supplier_document_type=data.supplier_document_type,
        supplier_document_series=data.supplier_document_series, supplier_document_number=data.supplier_document_number,
        supplier_document_date=datetime.combine(data.supplier_document_date, time.min, tzinfo=timezone.utc) if data.supplier_document_date else None,
        supplier_tax_amount=data.supplier_tax_amount,
        payment_term_id=payment_term.id if payment_term else None,
        payment_term_code=payment_term.code if payment_term else None,
        payment_term_name=payment_term.name if payment_term else None,
        payment_term_days_due=payment_term.days_due if payment_term else None,
        due_date=due_date,
        tax_rate_id=tax_rate.id,
        tax_rate_code=tax_rate.code,
        tax_rate_name=tax_rate.name,
        tax_rate_percent=tax_rate.rate,
    )
    db.add(purchase)
    try:
        db.flush()
        db.add(AccountPayable(tenant_id=actor.tenant_id, purchase_id=purchase.id, supplier_id=data.supplier_id, original_amount=total, balance_amount=total, status="open", due_date=due_date))
        for line in data.lines:
            db.add(PurchaseItem(tenant_id=actor.tenant_id, purchase_id=purchase.id, product_id=line.product_id, quantity=line.quantity, unit_cost=line.unit_cost, line_total=line.quantity * line.unit_cost))
            stock_before = db.execute(__import__("sqlalchemy").text("select coalesce(quantity,0) from inventories where tenant_id=:tenant and product_id=:product and warehouse_id=:warehouse for update"), {"tenant": str(actor.tenant_id), "product": str(line.product_id), "warehouse": str(data.warehouse_id)}).scalar() or Decimal("0")
            product_cost = db.scalar(select(Product).where(Product.id == line.product_id, Product.tenant_id == actor.tenant_id).with_for_update())
            if product_cost and product_cost.cost_method == "weighted_average":
                base_value = stock_before * product_cost.average_cost
                product_cost.average_cost = ((base_value + line.quantity * line.unit_cost) / (stock_before + line.quantity)).quantize(Decimal("0.01"))
            receive_tracked_stock(
                db, tenant_id=actor.tenant_id, actor_id=actor.user_id, product=product_cost,
                warehouse_id=data.warehouse_id, quantity=line.quantity, unit_cost=line.unit_cost,
                reference=number, lot_number=line.lot_number,
                expiration_date=line.expiration_date, serial_numbers=line.serial_numbers,
            )
        net, calculated_tax = split_tax(total, tax_rate.rate)
        tax = data.supplier_tax_amount if data.supplier_tax_amount is not None else calculated_tax
        net = total - tax
        purchase.taxable_amount = net
        purchase.tax_amount = tax
        post_entry(db, tenant_id=actor.tenant_id, actor_id=actor.user_id, number=f"AS-{number}", description=f"Compra {number}", source_type="purchase", source_id=purchase.id, lines=[("2010", net, Decimal("0")), ("4011", tax, Decimal("0")), ("4210", Decimal("0"), total)])
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="receive", module="purchases", entity="purchase", entity_id=purchase.id)
        complete_idempotency(
            db,
            claim=idempotency,
            response_body=PurchaseOut.model_validate(purchase).model_dump(mode="json"),
            response_status=status.HTTP_201_CREATED,
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
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
            product = db.scalar(select(Product).where(
                Product.id == item.product_id,
                Product.tenant_id == actor.tenant_id,
            ).with_for_update())
            if not product:
                raise HTTPException(status_code=409, detail="No se encontró el producto de una línea de compra")
            reverse_tracked_purchase_receipt(
                db, tenant_id=actor.tenant_id, product=product, warehouse_id=purchase.warehouse_id,
                quantity=item.quantity, purchase_reference=purchase.number,
                reversal_reference=f"CANCEL-{purchase.number}",
            )
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
