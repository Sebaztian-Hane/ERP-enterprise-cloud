from decimal import Decimal
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import Customer, Inventory, Payment, Product, Purchase, Sale
from app.security.auth import Actor, require_permission

router = APIRouter(prefix="/reports", tags=["reports"])


class DashboardOut(BaseModel):
    customers: int
    products: int
    stock_units: Decimal
    sales_total: Decimal
    purchases_total: Decimal
    receipts_total: Decimal
    payments_total: Decimal


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(actor: Actor = Depends(require_permission("reports:read")), db: Session = Depends(get_db)):
    tenant = actor.tenant_id
    total = lambda statement: db.scalar(statement) or Decimal("0")
    return DashboardOut(
        customers=total(select(func.count(Customer.id)).where(Customer.tenant_id == tenant)),
        products=total(select(func.count(Product.id)).where(Product.tenant_id == tenant)),
        stock_units=total(select(func.coalesce(func.sum(Inventory.quantity), 0)).where(Inventory.tenant_id == tenant)),
        sales_total=total(select(func.coalesce(func.sum(Sale.total), 0)).where(Sale.tenant_id == tenant, Sale.status == "posted")),
        purchases_total=total(select(func.coalesce(func.sum(Purchase.total), 0)).where(Purchase.tenant_id == tenant, Purchase.status == "received")),
        receipts_total=total(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.tenant_id == tenant, Payment.payment_type == "receipt")),
        payments_total=total(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.tenant_id == tenant, Payment.payment_type == "payment")),
    )

@router.get("/receivables")
def receivables(actor: Actor = Depends(require_permission("reports:read")), db: Session = Depends(get_db)):
    from sqlalchemy import text
    return db.execute(text("select * from accounts_receivable where tenant_id=:t and status in ('open','partial') order by created_at"),{"t":str(actor.tenant_id)}).mappings().all()

@router.get("/payables")
def payables(actor: Actor = Depends(require_permission("reports:read")), db: Session = Depends(get_db)):
    from sqlalchemy import text
    return db.execute(text("select * from accounts_payable where tenant_id=:t and status in ('open','partial') order by created_at"),{"t":str(actor.tenant_id)}).mappings().all()

@router.get("/low-stock")
def low_stock(actor: Actor = Depends(require_permission("reports:read")), db: Session = Depends(get_db)):
    from sqlalchemy import text
    return db.execute(text("""
      select p.id as product_id,p.sku,p.name,p.minimum_stock,
             coalesce(sum(i.quantity),0) as current_stock
      from products p left join inventories i on i.product_id=p.id and i.tenant_id=p.tenant_id
      where p.tenant_id=:t and p.is_active=true
      group by p.id,p.sku,p.name,p.minimum_stock
      having coalesce(sum(i.quantity),0) <= p.minimum_stock
      order by current_stock,p.name
    """),{"t":str(actor.tenant_id)}).mappings().all()
