from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from app.core.rate_limit import limiter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import AccountPayable, AccountReceivable, Customer, Payment, PaymentAllocation, Supplier
from app.schemas.payments import PaymentCreate, PaymentOut
from app.security.auth import Actor, require_permission
from app.services.audit import record

router = APIRouter(prefix="/payments", tags=["payments"])


@router.get("", response_model=list[PaymentOut])
def list_payments(actor: Actor = Depends(require_permission("payments:read")), db: Session = Depends(get_db)):
    return db.scalars(select(Payment).where(Payment.tenant_id == actor.tenant_id).order_by(Payment.created_at.desc())).all()


@router.post("", response_model=PaymentOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("20/minute")
def create_payment(request: Request, response: Response, data: PaymentCreate, actor: Actor = Depends(require_permission("payments:write")), db: Session = Depends(get_db)):
    if data.customer_id and not db.scalar(select(Customer.id).where(Customer.id == data.customer_id, Customer.tenant_id == actor.tenant_id, Customer.is_active.is_(True))):
        raise HTTPException(status_code=404, detail="Cliente no encontrado en la empresa activa")
    if data.supplier_id and not db.scalar(select(Supplier.id).where(Supplier.id == data.supplier_id, Supplier.tenant_id == actor.tenant_id, Supplier.is_active.is_(True))):
        raise HTTPException(status_code=404, detail="Proveedor no encontrado en la empresa activa")
    if not db.execute(__import__("sqlalchemy").text("select 1 from cash_accounts where id=:id and tenant_id=:tenant and is_active=true"), {"id":str(data.cash_account_id),"tenant":str(actor.tenant_id)}).first():
        raise HTTPException(status_code=404, detail="Cuenta de caja o banco no encontrada")
    payment_data = data.model_dump(exclude={"allocations"})
    payment = Payment(tenant_id=actor.tenant_id, **payment_data)
    db.add(payment)
    try:
        db.flush()
        for allocation in data.allocations:
            if data.payment_type == "receipt":
                account = db.scalar(select(AccountReceivable).where(AccountReceivable.id == allocation.receivable_id, AccountReceivable.tenant_id == actor.tenant_id, AccountReceivable.customer_id == data.customer_id).with_for_update())
                if not account or allocation.payable_id or allocation.amount > account.balance_amount:
                    raise HTTPException(status_code=422, detail="Aplicación de cobro inválida o saldo insuficiente")
                account.balance_amount -= allocation.amount
                account.status = "paid" if account.balance_amount == 0 else "partial"
                db.add(PaymentAllocation(tenant_id=actor.tenant_id, payment_id=payment.id, receivable_id=account.id, amount=allocation.amount))
            else:
                account = db.scalar(select(AccountPayable).where(AccountPayable.id == allocation.payable_id, AccountPayable.tenant_id == actor.tenant_id, AccountPayable.supplier_id == data.supplier_id).with_for_update())
                if not account or allocation.receivable_id or allocation.amount > account.balance_amount:
                    raise HTTPException(status_code=422, detail="Aplicación de pago inválida o saldo insuficiente")
                account.balance_amount -= allocation.amount
                account.status = "paid" if account.balance_amount == 0 else "partial"
                db.add(PaymentAllocation(tenant_id=actor.tenant_id, payment_id=payment.id, payable_id=account.id, amount=allocation.amount))
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="create", module="payments", entity="payment", entity_id=payment.id)
        db.execute(__import__("sqlalchemy").text("insert into cash_movements(tenant_id,cash_account_id,payment_id,movement_type,amount,reference) values(:tenant,:account,:payment,:type,:amount,:reference)"), {"tenant":str(actor.tenant_id),"account":str(data.cash_account_id),"payment":str(payment.id),"type":"inflow" if data.payment_type=="receipt" else "outflow","amount":data.amount,"reference":data.number})
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El número de cobro o pago ya existe en esta empresa") from exc
    db.refresh(payment)
    return payment
