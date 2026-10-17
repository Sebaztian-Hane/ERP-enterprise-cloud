from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import AccountReceivable, Customer, Product, Sale, SaleItem, Warehouse
from app.schemas.transactions import CancelTransaction, SaleCreate, SaleOut
from app.security.auth import Actor, require_permission
from app.services.audit import record
from app.services.stock import change_stock

router = APIRouter(prefix="/sales", tags=["sales"])


@router.get("/warehouses")
def list_sale_warehouses(actor: Actor = Depends(require_permission("sales:read")), db: Session = Depends(get_db)):
    """Expose only eligible warehouse identities needed when recording a sale."""
    return db.scalars(select(Warehouse).where(
        Warehouse.tenant_id == actor.tenant_id,
        Warehouse.is_active.is_(True),
    ).order_by(Warehouse.name)).all()


@router.get("", response_model=list[SaleOut])
def list_sales(actor: Actor = Depends(require_permission("sales:read")), db: Session = Depends(get_db)):
    return db.scalars(select(Sale).where(Sale.tenant_id == actor.tenant_id).order_by(Sale.created_at.desc())).all()


@router.post("", response_model=SaleOut, status_code=status.HTTP_201_CREATED)
def post_sale(data: SaleCreate, actor: Actor = Depends(require_permission("sales:write")), db: Session = Depends(get_db)):
    customer = db.scalar(select(Customer.id).where(Customer.id == data.customer_id, Customer.tenant_id == actor.tenant_id, Customer.is_active.is_(True)))
    warehouse = db.scalar(select(Warehouse.id).where(Warehouse.id == data.warehouse_id, Warehouse.tenant_id == actor.tenant_id, Warehouse.is_active.is_(True)))
    product_ids = {line.product_id for line in data.lines}
    products = set(db.scalars(select(Product.id).where(Product.tenant_id == actor.tenant_id, Product.id.in_(product_ids), Product.is_active.is_(True))).all())
    if not customer or not warehouse or products != product_ids:
        raise HTTPException(status_code=404, detail="Cliente, almacén o producto no pertenece a la empresa activa")
    total = sum((line.quantity * line.unit_price for line in data.lines), Decimal("0"))
    sale = Sale(tenant_id=actor.tenant_id, number=data.number, customer_id=data.customer_id, warehouse_id=data.warehouse_id, status="posted", total=total, note=data.note)
    db.add(sale)
    try:
        db.flush()
        db.add(AccountReceivable(tenant_id=actor.tenant_id, sale_id=sale.id, customer_id=data.customer_id, original_amount=total, balance_amount=total, status="open"))
        for line in data.lines:
            db.add(SaleItem(tenant_id=actor.tenant_id, sale_id=sale.id, product_id=line.product_id, quantity=line.quantity, unit_price=line.unit_price, line_total=line.quantity * line.unit_price))
            change_stock(db, tenant_id=actor.tenant_id, product_id=line.product_id, warehouse_id=data.warehouse_id, quantity=-line.quantity, movement_type="issue", reference=data.number)
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="post", module="sales", entity="sale", entity_id=sale.id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El número de venta ya existe en esta empresa") from exc
    db.refresh(sale)
    return sale


@router.post("/{sale_id}/cancel", response_model=SaleOut)
def cancel_sale(sale_id: str, data: CancelTransaction, actor: Actor = Depends(require_permission("sales:write")), db: Session = Depends(get_db)):
    sale = db.scalar(select(Sale).where(Sale.id == sale_id, Sale.tenant_id == actor.tenant_id).with_for_update())
    account = db.scalar(select(AccountReceivable).where(AccountReceivable.sale_id == sale_id, AccountReceivable.tenant_id == actor.tenant_id).with_for_update())
    if not sale or sale.status != "posted" or not account:
        raise HTTPException(status_code=409, detail="La venta no se puede cancelar")
    if account.balance_amount != account.original_amount:
        raise HTTPException(status_code=409, detail="No se puede cancelar una venta con cobros aplicados")
    items = db.scalars(select(SaleItem).where(SaleItem.sale_id == sale.id, SaleItem.tenant_id == actor.tenant_id)).all()
    for item in items:
        change_stock(db, tenant_id=actor.tenant_id, product_id=item.product_id, warehouse_id=sale.warehouse_id, quantity=item.quantity, movement_type="receipt", reference=f"CANCEL-{sale.number}")
    sale.status = "cancelled"
    account.status = "cancelled"
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="cancel", module="sales", entity="sale", entity_id=sale.id)
    db.commit()
    db.refresh(sale)
    return sale
