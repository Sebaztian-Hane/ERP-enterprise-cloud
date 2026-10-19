from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import Customer, Inventory, Payment, Product, Purchase, Sale
from app.security.auth import Actor, require_permission
from app.core.config import get_settings
from app.services.business_insights import generate_business_insight

router = APIRouter(prefix="/reports", tags=["reports"])


class DashboardOut(BaseModel):
    customers: int
    products: int
    stock_units: Decimal
    sales_total: Decimal
    purchases_total: Decimal
    receipts_total: Decimal
    payments_total: Decimal


class PeriodOut(BaseModel):
    start_date: date
    end_date: date
    sales_total: Decimal
    purchases_total: Decimal
    receipts_total: Decimal
    payments_total: Decimal


class ReportSummaryOut(BaseModel):
    current: PeriodOut
    comparison: PeriodOut | None = None


class ActivityPointOut(BaseModel):
    date: date
    sales_total: Decimal
    purchases_total: Decimal
    receipts_total: Decimal
    payments_total: Decimal


class InsightOut(BaseModel):
    status: str
    message: str | None = None
    headline: str | None = None
    summary: str | None = None
    recommendation: str | None = None
    severity: str | None = None
    operation_count: int | None = None
    minimum_operations: int | None = None
    cached: bool | None = None


def period_bounds(start_date: date, end_date: date):
    if end_date < start_date:
        raise HTTPException(status_code=422, detail="La fecha final debe ser posterior a la fecha inicial")
    return (
        datetime.combine(start_date, time.min, tzinfo=timezone.utc),
        datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone.utc),
    )


def totals_for_period(db: Session, tenant, start_date: date, end_date: date) -> PeriodOut:
    start, end = period_bounds(start_date, end_date)
    total = lambda statement: db.scalar(statement) or Decimal("0")
    window = lambda model: (model.tenant_id == tenant, model.created_at >= start, model.created_at < end)
    return PeriodOut(
        start_date=start_date,
        end_date=end_date,
        sales_total=total(select(func.coalesce(func.sum(Sale.total), 0)).where(*window(Sale), Sale.status == "posted")),
        purchases_total=total(select(func.coalesce(func.sum(Purchase.total), 0)).where(*window(Purchase), Purchase.status == "received")),
        receipts_total=total(select(func.coalesce(func.sum(Payment.amount), 0)).where(*window(Payment), Payment.payment_type == "receipt")),
        payments_total=total(select(func.coalesce(func.sum(Payment.amount), 0)).where(*window(Payment), Payment.payment_type == "payment")),
    )


def activity_for_period(db: Session, tenant, start_date: date, end_date: date) -> list[ActivityPointOut]:
    """Return a gap-free daily activity series for charts and exports.

    Aggregating in Python avoids database-specific date truncation functions and
    makes the endpoint behave consistently in local SQLite and PostgreSQL.
    """
    start, end = period_bounds(start_date, end_date)
    buckets = {
        start_date + timedelta(days=offset): {
            "sales_total": Decimal("0"), "purchases_total": Decimal("0"),
            "receipts_total": Decimal("0"), "payments_total": Decimal("0"),
        }
        for offset in range((end_date - start_date).days + 1)
    }

    def add_rows(model, amount_field, target, *criteria):
        rows = db.execute(
            select(model.created_at, amount_field).where(
                model.tenant_id == tenant, model.created_at >= start,
                model.created_at < end, *criteria,
            )
        ).all()
        for created_at, amount in rows:
            day = created_at.date()
            if day in buckets:
                buckets[day][target] += Decimal(amount or 0)

    add_rows(Sale, Sale.total, "sales_total", Sale.status == "posted")
    add_rows(Purchase, Purchase.total, "purchases_total", Purchase.status == "received")
    add_rows(Payment, Payment.amount, "receipts_total", Payment.payment_type == "receipt")
    add_rows(Payment, Payment.amount, "payments_total", Payment.payment_type == "payment")
    return [ActivityPointOut(date=day, **values) for day, values in buckets.items()]


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


@router.get("/summary", response_model=ReportSummaryOut)
def report_summary(
    start_date: date = Query(...),
    end_date: date = Query(...),
    compare_start_date: date | None = Query(None),
    compare_end_date: date | None = Query(None),
    actor: Actor = Depends(require_permission("reports:read")),
    db: Session = Depends(get_db),
):
    if (compare_start_date is None) != (compare_end_date is None):
        raise HTTPException(status_code=422, detail="Indique ambas fechas para el comparativo")
    comparison = totals_for_period(db, actor.tenant_id, compare_start_date, compare_end_date) if compare_start_date and compare_end_date else None
    return ReportSummaryOut(current=totals_for_period(db, actor.tenant_id, start_date, end_date), comparison=comparison)


@router.get("/activity", response_model=list[ActivityPointOut])
def report_activity(
    start_date: date = Query(...),
    end_date: date = Query(...),
    actor: Actor = Depends(require_permission("reports:read")),
    db: Session = Depends(get_db),
):
    return activity_for_period(db, actor.tenant_id, start_date, end_date)


@router.get("/insight", response_model=InsightOut)
def report_insight(
    start_date: date = Query(...), end_date: date = Query(...),
    actor: Actor = Depends(require_permission("reports:read")), db: Session = Depends(get_db),
):
    current = totals_for_period(db, actor.tenant_id, start_date, end_date)
    start, end = period_bounds(start_date, end_date)
    window = lambda model: (model.tenant_id == actor.tenant_id, model.created_at >= start, model.created_at < end)
    count = lambda model, *criteria: int(db.scalar(select(func.count(model.id)).where(*window(model), *criteria)) or 0)
    operation_count = count(Sale, Sale.status == "posted") + count(Purchase, Purchase.status == "received") + count(Payment)
    from sqlalchemy import text
    receivable_total = db.scalar(text("select coalesce(sum(balance_amount), 0) from accounts_receivable where tenant_id=:t and status in ('open','partial')"), {"t": str(actor.tenant_id)}) or Decimal("0")
    payable_total = db.scalar(text("select coalesce(sum(balance_amount), 0) from accounts_payable where tenant_id=:t and status in ('open','partial')"), {"t": str(actor.tenant_id)}) or Decimal("0")
    low_stock_count = len(low_stock(actor, db))
    return generate_business_insight(
        settings=get_settings(), tenant_id=actor.tenant_id, start_date=str(start_date), end_date=str(end_date),
        operation_count=operation_count, sales_total=current.sales_total, purchases_total=current.purchases_total,
        receipts_total=current.receipts_total, payments_total=current.payments_total,
        receivable_total=Decimal(receivable_total), payable_total=Decimal(payable_total), low_stock_count=low_stock_count,
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
