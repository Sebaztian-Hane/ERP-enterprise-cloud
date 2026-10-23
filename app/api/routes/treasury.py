from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.petty_cash import PettyCashExpenseCreate, PettyCashFundCreate, PettyCashReplenishmentCreate
from app.security.auth import Actor, require_permission
from app.services.accounting import post_entry
from app.services.approval_documents import create_petty_cash_evidence
from app.services.audit import record

router = APIRouter(prefix="/treasury", tags=["treasury"])

class AccountIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    account_type: str = Field(pattern="^(cash|bank)$")
    currency: str = Field(default="PEN", min_length=3, max_length=3)

class MovementIn(BaseModel):
    cash_account_id: UUID
    movement_type: str = Field(pattern="^(inflow|outflow)$")
    amount: Decimal = Field(gt=0)
    payment_id: UUID | None = None
    reference: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=500)

def _account(db: Session, tenant_id: UUID, account_id: UUID, cash_only: bool = False) -> dict:
    row = db.execute(text("select * from cash_accounts where id=:id and tenant_id=:tenant"), {"id": str(account_id), "tenant": str(tenant_id)}).mappings().first()
    if not row: raise HTTPException(404, "Cuenta de tesorería no encontrada")
    if cash_only and row["account_type"] != "cash": raise HTTPException(422, "La cuenta operativa debe ser de tipo caja")
    return row

def _fund(db: Session, tenant_id: UUID, fund_id: UUID, lock: bool = False) -> dict:
    suffix = " for update" if lock else ""
    row = db.execute(text(f"select * from petty_cash_funds where id=:id and tenant_id=:tenant{suffix}"), {"id": str(fund_id), "tenant": str(tenant_id)}).mappings().first()
    if not row: raise HTTPException(404, "Caja chica no encontrada")
    return row

def _approval(db: Session, tenant_id: UUID, action: str, entity_type: str, entity_id: UUID, amount: Decimal, actor_id: UUID) -> UUID:
    policy_id = db.execute(text("""select id from approval_policies where tenant_id=:tenant and module='treasury' and action=:action and is_active=true and min_amount<=:amount order by min_amount desc limit 1"""), {"tenant": str(tenant_id), "action": action, "amount": amount}).scalar()
    return db.execute(text("""insert into approval_requests(tenant_id,policy_id,module,action,entity_type,entity_id,amount,payload,requested_by) values(:tenant,:policy,'treasury',:action,:entity_type,:entity_id,:amount,'{}'::jsonb,:actor) returning id"""), {"tenant": str(tenant_id), "policy": str(policy_id) if policy_id else None, "action": action, "entity_type": entity_type, "entity_id": str(entity_id), "amount": amount, "actor": str(actor_id)}).scalar_one()

@router.get("/accounts")
def accounts(actor: Actor = Depends(require_permission("treasury:read")), db: Session = Depends(get_db)):
    return db.execute(text("""select c.*,coalesce(sum(case when m.movement_type='inflow' then m.amount else -m.amount end),0) as balance from cash_accounts c left join cash_movements m on m.cash_account_id=c.id and m.tenant_id=c.tenant_id where c.tenant_id=:tenant group by c.id order by c.name"""), {"tenant": str(actor.tenant_id)}).mappings().all()

@router.post("/accounts", status_code=status.HTTP_201_CREATED)
def create_account(data: AccountIn, actor: Actor = Depends(require_permission("treasury:write")), db: Session = Depends(get_db)):
    try:
        row = db.execute(text("insert into cash_accounts(tenant_id,code,name,account_type,currency) values(:tenant,:code,:name,:kind,:currency) returning *"), {"tenant": str(actor.tenant_id), "code": data.code, "name": data.name, "kind": data.account_type, "currency": data.currency.upper()}).mappings().one()
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="create", module="treasury", entity="cash_account", entity_id=row["id"]); db.commit(); return row
    except Exception as exc:
        db.rollback(); raise HTTPException(409, "Código de cuenta duplicado") from exc

@router.post("/movements", status_code=status.HTTP_201_CREATED)
def movement(data: MovementIn, actor: Actor = Depends(require_permission("treasury:write")), db: Session = Depends(get_db)):
    _account(db, actor.tenant_id, data.cash_account_id)
    row = db.execute(text("""insert into cash_movements(tenant_id,cash_account_id,payment_id,movement_type,amount,reference,note) values(:tenant,:account,:payment,:kind,:amount,:reference,:note) returning *"""), {"tenant": str(actor.tenant_id), "account": str(data.cash_account_id), "payment": str(data.payment_id) if data.payment_id else None, "kind": data.movement_type, "amount": data.amount, "reference": data.reference, "note": data.note}).mappings().one()
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="create", module="treasury", entity="cash_movement", entity_id=row["id"]); db.commit(); return row

@router.get("/petty-cash/funds")
def petty_cash_funds(actor: Actor = Depends(require_permission("treasury:read")), db: Session = Depends(get_db)):
    return db.execute(text("""select f.*,c.name as cash_account_name,s.name as funding_account_name,coalesce((select count(*) from petty_cash_expenses e where e.fund_id=f.id and e.status='submitted'),0) as pending_expenses,coalesce((select count(*) from petty_cash_replenishments r where r.fund_id=f.id and r.status='submitted'),0) as pending_replenishments from petty_cash_funds f join cash_accounts c on c.id=f.cash_account_id join cash_accounts s on s.id=f.funding_cash_account_id where f.tenant_id=:tenant order by f.name"""), {"tenant": str(actor.tenant_id)}).mappings().all()

@router.post("/petty-cash/funds", status_code=status.HTTP_201_CREATED)
def create_petty_cash_fund(data: PettyCashFundCreate, actor: Actor = Depends(require_permission("treasury:write")), db: Session = Depends(get_db)):
    cash, source = _account(db, actor.tenant_id, data.cash_account_id, True), _account(db, actor.tenant_id, data.funding_cash_account_id)
    if cash["currency"] != source["currency"]: raise HTTPException(422, "Las cuentas deben usar la misma moneda")
    try:
        fund = db.execute(text("""insert into petty_cash_funds(tenant_id,code,name,cash_account_id,funding_cash_account_id,custodian_user_id,fixed_amount,current_balance,currency,created_by) select :tenant,:code,:name,:cash,:source,:custodian,:amount,:amount,:currency,:actor where exists(select 1 from tenant_memberships where tenant_id=:tenant and user_id=:custodian and status='active') returning *"""), {"tenant": str(actor.tenant_id), "code": data.code, "name": data.name, "cash": str(data.cash_account_id), "source": str(data.funding_cash_account_id), "custodian": str(data.custodian_user_id), "amount": data.fixed_amount, "currency": cash["currency"], "actor": str(actor.user_id)}).mappings().first()
        if not fund: raise HTTPException(422, "El custodio debe ser usuario activo de esta empresa")
        db.execute(text("""insert into cash_movements(tenant_id,cash_account_id,movement_type,amount,reference,note,petty_cash_fund_id) values(:tenant,:source,'outflow',:amount,:reference,'Constitución de caja chica',:fund),(:tenant,:cash,'inflow',:amount,:reference,'Constitución de caja chica',:fund)"""), {"tenant": str(actor.tenant_id), "source": str(data.funding_cash_account_id), "cash": str(data.cash_account_id), "amount": data.fixed_amount, "reference": f"CC-{data.code}", "fund": str(fund["id"])})
        post_entry(db, tenant_id=actor.tenant_id, actor_id=actor.user_id, number=f"CC-{data.code}-AP", description=f"Constitución caja chica {data.name}", source_type="petty_cash_fund", source_id=fund["id"], lines=[("1020", data.fixed_amount, Decimal("0")), ("1010", Decimal("0"), data.fixed_amount)])
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="create", module="treasury", entity="petty_cash_fund", entity_id=fund["id"]); db.commit(); return fund
    except HTTPException: db.rollback(); raise
    except Exception as exc: db.rollback(); raise HTTPException(409, "No se pudo constituir la caja chica") from exc

@router.get("/petty-cash/funds/{fund_id}/expenses")
def petty_cash_expenses(fund_id: UUID, actor: Actor = Depends(require_permission("treasury:read")), db: Session = Depends(get_db)):
    _fund(db, actor.tenant_id, fund_id)
    return db.execute(text("select * from petty_cash_expenses where tenant_id=:tenant and fund_id=:fund order by created_at desc"), {"tenant": str(actor.tenant_id), "fund": str(fund_id)}).mappings().all()

@router.post("/petty-cash/funds/{fund_id}/expenses", status_code=status.HTTP_201_CREATED)
def create_petty_cash_expense(fund_id: UUID, data: PettyCashExpenseCreate, actor: Actor = Depends(require_permission("treasury:write")), db: Session = Depends(get_db)):
    fund = _fund(db, actor.tenant_id, fund_id)
    if fund["status"] != "active": raise HTTPException(409, "La caja chica no está activa")
    if data.document_id and not db.execute(text("select 1 from documents where id=:id and tenant_id=:tenant"), {"id": str(data.document_id), "tenant": str(actor.tenant_id)}).first(): raise HTTPException(422, "El documento no pertenece a la empresa")
    expense = db.execute(text("""insert into petty_cash_expenses(tenant_id,fund_id,number,expense_date,description,merchant_name,supplier_tax_id,receipt_number,amount,expense_account_code,cost_center_id,document_id,created_by) values(:tenant,:fund,:number,:date,:description,:merchant,:tax_id,:receipt,:amount,:account,:cost_center,:document,:actor) returning *"""), {"tenant": str(actor.tenant_id), "fund": str(fund_id), "number": data.number, "date": data.expense_date, "description": data.description, "merchant": data.merchant_name, "tax_id": data.supplier_tax_id, "receipt": data.receipt_number, "amount": data.amount, "account": data.expense_account_code, "cost_center": str(data.cost_center_id) if data.cost_center_id else None, "document": str(data.document_id) if data.document_id else None, "actor": str(actor.user_id)}).mappings().one()
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="create", module="treasury", entity="petty_cash_expense", entity_id=expense["id"]); db.commit(); return expense

@router.post("/petty-cash/expenses/{expense_id}/submit")
def submit_petty_cash_expense(expense_id: UUID, actor: Actor = Depends(require_permission("treasury:write")), db: Session = Depends(get_db)):
    expense = db.execute(text("select * from petty_cash_expenses where id=:id and tenant_id=:tenant and status='draft' and created_by=:actor for update"), {"id": str(expense_id), "tenant": str(actor.tenant_id), "actor": str(actor.user_id)}).mappings().first()
    if not expense: raise HTTPException(409, "El vale no está disponible para envío")
    request_id = _approval(db, actor.tenant_id, "petty_cash_expense", "petty_cash_expense", expense_id, expense["amount"], actor.user_id)
    db.execute(text("update petty_cash_expenses set status='submitted',approval_request_id=:request,updated_at=now() where id=:id"), {"request": str(request_id), "id": str(expense_id)})
    create_petty_cash_evidence(db, tenant_id=actor.tenant_id, entity_type="petty_cash_expense", entity_id=expense_id, approval_request_id=request_id, requested_by=actor.user_id)
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="submit", module="treasury", entity="petty_cash_expense", entity_id=expense_id); db.commit(); return {"id": expense_id, "approval_request_id": request_id, "status": "submitted"}

@router.post("/petty-cash/expenses/{expense_id}/post")
def post_petty_cash_expense(expense_id: UUID, actor: Actor = Depends(require_permission("treasury:write")), db: Session = Depends(get_db)):
    expense = db.execute(text("""select e.*,f.cash_account_id,f.current_balance,f.status as fund_status from petty_cash_expenses e join petty_cash_funds f on f.id=e.fund_id and f.tenant_id=e.tenant_id where e.id=:id and e.tenant_id=:tenant and e.status='approved' for update"""), {"id": str(expense_id), "tenant": str(actor.tenant_id)}).mappings().first()
    if not expense: raise HTTPException(409, "El vale debe estar aprobado antes de contabilizarse")
    if str(expense["created_by"]) == str(actor.user_id) or str(expense["approved_by"]) == str(actor.user_id): raise HTTPException(403, "La separación de funciones exige otro ejecutor")
    if expense["fund_status"] != "active" or Decimal(str(expense["current_balance"])) < Decimal(str(expense["amount"])): raise HTTPException(409, "Saldo insuficiente o caja inactiva")
    db.execute(text("insert into cash_movements(tenant_id,cash_account_id,movement_type,amount,reference,note,petty_cash_expense_id) values(:tenant,:account,'outflow',:amount,:reference,:note,:expense)"), {"tenant": str(actor.tenant_id), "account": str(expense["cash_account_id"]), "amount": expense["amount"], "reference": expense["number"], "note": expense["description"], "expense": str(expense_id)})
    db.execute(text("update petty_cash_funds set current_balance=current_balance-:amount,updated_at=now() where id=:fund and tenant_id=:tenant"), {"amount": expense["amount"], "fund": str(expense["fund_id"]), "tenant": str(actor.tenant_id)})
    db.execute(text("update petty_cash_expenses set status='posted',posted_by=:actor,posted_at=now(),updated_at=now() where id=:id"), {"actor": str(actor.user_id), "id": str(expense_id)})
    line = (expense["expense_account_code"], Decimal(str(expense["amount"])), Decimal("0"), expense["cost_center_id"]) if expense["cost_center_id"] else (expense["expense_account_code"], Decimal(str(expense["amount"])), Decimal("0"))
    post_entry(db, tenant_id=actor.tenant_id, actor_id=actor.user_id, number=f"CCV-{expense['number']}", description=expense["description"], source_type="petty_cash_expense", source_id=expense_id, lines=[line, ("1020", Decimal("0"), Decimal(str(expense["amount"])))], entry_date=expense["expense_date"])
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="post", module="treasury", entity="petty_cash_expense", entity_id=expense_id); db.commit(); return {"id": expense_id, "status": "posted"}

@router.post("/petty-cash/funds/{fund_id}/replenishments", status_code=status.HTTP_201_CREATED)
def request_replenishment(fund_id: UUID, data: PettyCashReplenishmentCreate, actor: Actor = Depends(require_permission("treasury:write")), db: Session = Depends(get_db)):
    fund = _fund(db, actor.tenant_id, fund_id, True)
    expected = Decimal(str(fund["fixed_amount"])) - Decimal(str(fund["current_balance"]))
    if fund["status"] != "active" or data.amount != expected: raise HTTPException(422, "La reposición debe coincidir con el saldo pendiente de una caja activa")
    row = db.execute(text("insert into petty_cash_replenishments(tenant_id,fund_id,number,amount,requested_by) values(:tenant,:fund,:number,:amount,:actor) returning *"), {"tenant": str(actor.tenant_id), "fund": str(fund_id), "number": data.number, "amount": data.amount, "actor": str(actor.user_id)}).mappings().one()
    request_id = _approval(db, actor.tenant_id, "petty_cash_replenishment", "petty_cash_replenishment", row["id"], data.amount, actor.user_id)
    db.execute(text("update petty_cash_replenishments set approval_request_id=:request where id=:id"), {"request": str(request_id), "id": str(row["id"])})
    create_petty_cash_evidence(db, tenant_id=actor.tenant_id, entity_type="petty_cash_replenishment", entity_id=row["id"], approval_request_id=request_id, requested_by=actor.user_id)
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="submit", module="treasury", entity="petty_cash_replenishment", entity_id=row["id"]); db.commit(); return {"id": row["id"], "approval_request_id": request_id, "status": "submitted"}

@router.post("/petty-cash/replenishments/{replenishment_id}/execute")
def execute_replenishment(replenishment_id: UUID, actor: Actor = Depends(require_permission("treasury:write")), db: Session = Depends(get_db)):
    row = db.execute(text("""select r.*,f.cash_account_id,f.funding_cash_account_id,f.status as fund_status from petty_cash_replenishments r join petty_cash_funds f on f.id=r.fund_id and f.tenant_id=r.tenant_id where r.id=:id and r.tenant_id=:tenant and r.status='approved' for update"""), {"id": str(replenishment_id), "tenant": str(actor.tenant_id)}).mappings().first()
    if not row: raise HTTPException(409, "La reposición debe estar aprobada")
    if str(row["requested_by"]) == str(actor.user_id) or str(row["approved_by"]) == str(actor.user_id): raise HTTPException(403, "La separación de funciones exige otro ejecutor")
    if row["fund_status"] != "active": raise HTTPException(409, "La caja chica no está activa")
    db.execute(text("""insert into cash_movements(tenant_id,cash_account_id,movement_type,amount,reference,note,petty_cash_replenishment_id) values(:tenant,:source,'outflow',:amount,:reference,'Reposición de caja chica',:id),(:tenant,:cash,'inflow',:amount,:reference,'Reposición de caja chica',:id)"""), {"tenant": str(actor.tenant_id), "source": str(row["funding_cash_account_id"]), "cash": str(row["cash_account_id"]), "amount": row["amount"], "reference": row["number"], "id": str(replenishment_id)})
    db.execute(text("update petty_cash_funds set current_balance=current_balance+:amount,updated_at=now() where id=:fund and tenant_id=:tenant"), {"amount": row["amount"], "fund": str(row["fund_id"]), "tenant": str(actor.tenant_id)})
    db.execute(text("update petty_cash_replenishments set status='executed',executed_by=:actor,executed_at=now(),updated_at=now() where id=:id"), {"actor": str(actor.user_id), "id": str(replenishment_id)})
    post_entry(db, tenant_id=actor.tenant_id, actor_id=actor.user_id, number=f"CCR-{row['number']}", description="Reposición de caja chica", source_type="petty_cash_replenishment", source_id=replenishment_id, lines=[("1020", Decimal(str(row["amount"])), Decimal("0")), ("1010", Decimal("0"), Decimal(str(row["amount"])))])
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="execute", module="treasury", entity="petty_cash_replenishment", entity_id=replenishment_id); db.commit(); return {"id": replenishment_id, "status": "executed"}

@router.get("/petty-cash/replenishments")
def petty_cash_replenishments(
    status_filter: str | None = None,
    actor: Actor = Depends(require_permission("treasury:read")),
    db: Session = Depends(get_db),
):
    return db.execute(text("""
        select r.*, f.name as fund_name, f.code as fund_code
        from petty_cash_replenishments r
        join petty_cash_funds f on f.id=r.fund_id and f.tenant_id=r.tenant_id
        where r.tenant_id=:tenant and (:status is null or r.status=:status)
        order by r.created_at desc
    """), {"tenant": str(actor.tenant_id), "status": status_filter}).mappings().all()
