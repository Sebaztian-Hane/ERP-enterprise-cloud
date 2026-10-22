from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from app.core.rate_limit import limiter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import AccountPayable, AccountReceivable, Customer, Payment, PaymentAllocation, Supplier
from app.schemas.payments import PaymentCreate, PaymentOut
from app.schemas.pagination import paginate
from app.security.auth import Actor, require_permission
from app.services.audit import record
from app.services.accounting import post_entry
from app.services.idempotency import claim as claim_idempotency, complete as complete_idempotency, replay_response

router = APIRouter(prefix="/payments", tags=["payments"])


def _same_payment_request(existing: Payment, data: PaymentCreate) -> bool:
    """Recognize a retried financial request without accepting changed data."""
    return (
        existing.payment_type == data.payment_type
        and existing.amount == data.amount
        and existing.method == data.method
        and existing.cash_account_id == data.cash_account_id
        and existing.customer_id == data.customer_id
        and existing.supplier_id == data.supplier_id
        and existing.reference == data.reference
        and existing.note == data.note
    )


@router.get("")
def list_payments(
    actor: Actor = Depends(require_permission("payments:read")),
    db: Session = Depends(get_db),
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=100),
):
    statement = select(Payment).where(Payment.tenant_id == actor.tenant_id).order_by(Payment.created_at.desc(), Payment.id)
    if not isinstance(page, int) and not isinstance(page_size, int):
        return db.scalars(statement).all()
    return paginate(db, statement, key="payments", page=page or 1, page_size=page_size or 25)


@router.post("", response_model=PaymentOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("20/minute")
def create_payment(request: Request, response: Response, data: PaymentCreate, actor: Actor = Depends(require_permission("payments:write")), db: Session = Depends(get_db)):
    idempotency = claim_idempotency(
        db,
        tenant_id=actor.tenant_id,
        actor_user_id=actor.user_id,
        operation="payments.create",
        key=request.headers.get("Idempotency-Key"),
        payload=data.model_dump(mode="json"),
    )
    if idempotency and idempotency.is_replay:
        return replay_response(idempotency)
    # A mobile connection can disappear after the database commits but before
    # the browser receives the response.  The user-visible number is therefore
    # an idempotency key for an otherwise identical retry.
    existing = db.scalar(select(Payment).where(
        Payment.tenant_id == actor.tenant_id,
        Payment.number == data.number,
    ))
    if existing:
        if _same_payment_request(existing, data):
            return existing
        raise HTTPException(status_code=409, detail="El número de cobro o pago ya existe con datos diferentes")
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
        post_entry(db, tenant_id=actor.tenant_id, actor_id=actor.user_id, number=f"AS-{data.number}", description=f"{'Cobro' if data.payment_type == 'receipt' else 'Pago'} {data.number}", source_type="payment", source_id=payment.id, lines=[("1010", data.amount if data.payment_type == "receipt" else Decimal("0"), data.amount if data.payment_type == "payment" else Decimal("0")), ("1210" if data.payment_type == "receipt" else "4210", Decimal("0") if data.payment_type == "receipt" else data.amount, data.amount if data.payment_type == "receipt" else Decimal("0"))])
        complete_idempotency(
            db,
            claim=idempotency,
            response_body=PaymentOut.model_validate(payment).model_dump(mode="json"),
            response_status=status.HTTP_201_CREATED,
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El número de cobro o pago ya existe en esta empresa") from exc
    db.refresh(payment)
    return payment
