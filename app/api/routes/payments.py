from decimal import Decimal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from app.core.rate_limit import limiter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import AccountPayable, AccountReceivable, Customer, Payment, PaymentAllocation, PosPaymentMethodConfig, Supplier
from app.schemas.payments import PaymentCreate, PaymentOut, PosPaymentMethodConfigOut, PosPaymentMethodConfigUpsert
from app.schemas.pagination import paginate
from app.security.auth import Actor, require_permission, require_second_factor
from app.services.audit import record
from app.services.accounting import post_entry
from app.services.approval_documents import create_payment_evidence
from app.services.idempotency import claim as claim_idempotency, complete as complete_idempotency, replay_response

router = APIRouter(prefix="/payments", tags=["payments"])


def _active_tenant_cash_account(db: Session, *, cash_account_id: UUID, tenant_id: UUID) -> bool:
    """Do not let a tenant configure or collect into another tenant's account."""
    return bool(db.execute(
        __import__("sqlalchemy").text(
            "select 1 from cash_accounts where id=:id and tenant_id=:tenant and is_active=true"
        ),
        {"id": str(cash_account_id), "tenant": str(tenant_id)},
    ).first())


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


@router.get("/pos-methods", response_model=list[PosPaymentMethodConfigOut])
def list_pos_payment_methods(
    actor: Actor = Depends(require_permission("payments:read")),
    db: Session = Depends(get_db),
):
    return db.scalars(
        select(PosPaymentMethodConfig)
        .where(PosPaymentMethodConfig.tenant_id == actor.tenant_id)
        .order_by(PosPaymentMethodConfig.method)
    ).all()


@router.put("/pos-methods", response_model=PosPaymentMethodConfigOut)
def upsert_pos_payment_method(
    data: PosPaymentMethodConfigUpsert,
    actor: Actor = Depends(require_permission("payments:write")),
    db: Session = Depends(get_db),
):
    if not _active_tenant_cash_account(db, cash_account_id=data.cash_account_id, tenant_id=actor.tenant_id):
        raise HTTPException(status_code=404, detail="Cuenta de caja o banco no encontrada en la empresa activa")
    config = db.scalar(
        select(PosPaymentMethodConfig)
        .where(
            PosPaymentMethodConfig.tenant_id == actor.tenant_id,
            PosPaymentMethodConfig.method == data.method,
        )
        .with_for_update()
    )
    if config is None:
        config = PosPaymentMethodConfig(tenant_id=actor.tenant_id, **data.model_dump())
        db.add(config)
        action = "create"
    else:
        config.cash_account_id = data.cash_account_id
        config.is_active = data.is_active
        action = "update"
    try:
        db.flush()
        record(
            db,
            tenant_id=actor.tenant_id,
            actor_user_id=actor.user_id,
            action=action,
            module="payments",
            entity="pos_payment_method",
            entity_id=config.id,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="No se pudo guardar el método de pago POS") from exc
    db.refresh(config)
    return config


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
    if not _active_tenant_cash_account(db, cash_account_id=data.cash_account_id, tenant_id=actor.tenant_id):
        raise HTTPException(status_code=404, detail="Cuenta de caja o banco no encontrada")
    payment_data = data.model_dump(exclude={"allocations"})
    payment = Payment(tenant_id=actor.tenant_id, status="submitted" if data.payment_type == "payment" else "posted", requested_by=actor.user_id if data.payment_type == "payment" else None, **payment_data)
    db.add(payment)
    try:
        db.flush()
        if data.payment_type == "payment":
            for allocation in data.allocations:
                account = db.scalar(select(AccountPayable).where(AccountPayable.id == allocation.payable_id, AccountPayable.tenant_id == actor.tenant_id, AccountPayable.supplier_id == data.supplier_id).with_for_update())
                if not account or allocation.receivable_id or allocation.amount > account.balance_amount:
                    raise HTTPException(status_code=422, detail="Aplicación de pago inválida o saldo insuficiente")
                db.add(PaymentAllocation(tenant_id=actor.tenant_id, payment_id=payment.id, payable_id=account.id, amount=allocation.amount))
            policy_id = db.execute(__import__("sqlalchemy").text("""
                select id from approval_policies where tenant_id=:tenant and module='payments'
                  and action='supplier_payment' and is_active=true and min_amount<=:amount
                  and warehouse_id is null and cost_center_id is null and area_code is null and risk_level is null
                order by min_amount desc limit 1
            """), {"tenant": str(actor.tenant_id), "amount": data.amount}).scalar()
            request_id = db.execute(__import__("sqlalchemy").text("""
                insert into approval_requests(tenant_id,policy_id,module,action,entity_type,entity_id,amount,payload,requested_by)
                values(:tenant,:policy,'payments','supplier_payment','supplier_payment',:payment,:amount,'{}'::jsonb,:actor) returning id
            """), {"tenant": str(actor.tenant_id), "policy": str(policy_id) if policy_id else None, "payment": str(payment.id), "amount": data.amount, "actor": str(actor.user_id)}).scalar_one()
            payment.approval_request_id = request_id
            create_payment_evidence(db, tenant_id=actor.tenant_id, payment_id=payment.id, approval_request_id=request_id, requested_by=actor.user_id)
            record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="submit", module="payments", entity="supplier_payment", entity_id=payment.id)
            complete_idempotency(db, claim=idempotency, response_body=PaymentOut.model_validate(payment).model_dump(mode="json"), response_status=status.HTTP_201_CREATED)
            db.commit(); db.refresh(payment); return payment
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


@router.post("/{payment_id}/execute", response_model=PaymentOut)
def execute_supplier_payment(payment_id: UUID, actor: Actor = Depends(require_permission("payments:write")), _: Actor = Depends(require_second_factor), db: Session = Depends(get_db)):
    payment = db.scalar(select(Payment).where(Payment.id == payment_id, Payment.tenant_id == actor.tenant_id, Payment.payment_type == "payment", Payment.status == "approved").with_for_update())
    if not payment:
        raise HTTPException(status_code=409, detail="El pago debe estar aprobado antes de ejecutarse")
    if actor.user_id in {payment.requested_by, payment.approved_by}:
        raise HTTPException(status_code=403, detail="La separación de funciones exige otro ejecutor")
    allocations = db.scalars(select(PaymentAllocation).where(PaymentAllocation.payment_id == payment.id, PaymentAllocation.tenant_id == actor.tenant_id)).all()
    try:
        for allocation in allocations:
            payable = db.scalar(select(AccountPayable).where(AccountPayable.id == allocation.payable_id, AccountPayable.tenant_id == actor.tenant_id).with_for_update())
            if not payable or allocation.amount > payable.balance_amount:
                raise HTTPException(status_code=409, detail="El saldo por pagar cambió antes de ejecutar el pago")
            payable.balance_amount -= allocation.amount
            payable.status = "paid" if payable.balance_amount == 0 else "partial"
        db.execute(__import__("sqlalchemy").text("insert into cash_movements(tenant_id,cash_account_id,payment_id,movement_type,amount,reference) values(:tenant,:account,:payment,'outflow',:amount,:reference)"), {"tenant": str(actor.tenant_id), "account": str(payment.cash_account_id), "payment": str(payment.id), "amount": payment.amount, "reference": payment.number})
        post_entry(db, tenant_id=actor.tenant_id, actor_id=actor.user_id, number=f"AS-{payment.number}", description=f"Pago {payment.number}", source_type="payment", source_id=payment.id, lines=[("1010", Decimal("0"), payment.amount), ("4210", payment.amount, Decimal("0"))])
        payment.status = "posted"; payment.executed_by = actor.user_id
        from datetime import datetime, timezone
        payment.executed_at = datetime.now(timezone.utc)
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="execute", module="payments", entity="supplier_payment", entity_id=payment.id)
        db.commit()
    except HTTPException:
        db.rollback(); raise
    db.refresh(payment)
    return payment
