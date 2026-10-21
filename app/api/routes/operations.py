from datetime import date
from decimal import Decimal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import Inventory, Product, Warehouse
from app.security.auth import Actor, require_permission
from app.services.audit import record
from app.services.approval_documents import append_internal_decision_evidence, create_inventory_count_evidence

router = APIRouter(prefix="/operations", tags=["operations"])


class ReservationIn(BaseModel):
    product_id: UUID
    warehouse_id: UUID
    reference: str = Field(min_length=1, max_length=100)
    reference_type: str = Field(default="sales_order", max_length=32)
    reference_id: UUID | None = None
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=0)
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("quantity")
    @classmethod
    def whole_units(cls, value: Decimal) -> Decimal:
        if value != value.to_integral_value():
            raise ValueError("La cantidad debe ser un número entero")
        return value


class CountLineIn(BaseModel):
    product_id: UUID
    counted_quantity: Decimal = Field(ge=0, max_digits=14, decimal_places=0)
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("counted_quantity")
    @classmethod
    def whole_units(cls, value: Decimal) -> Decimal:
        if value != value.to_integral_value():
            raise ValueError("La cantidad debe ser un número entero")
        return value


class CountIn(BaseModel):
    number: str = Field(min_length=1, max_length=40)
    warehouse_id: UUID
    reason: str = Field(min_length=3, max_length=240)
    count_date: date = Field(default_factory=date.today)
    note: str | None = Field(default=None, max_length=2000)
    lines: list[CountLineIn] = Field(min_length=1)


class DecisionIn(BaseModel):
    decision_note: str | None = Field(default=None, max_length=2000)


class PolicyIn(BaseModel):
    module: str = Field(min_length=2, max_length=40)
    action: str = Field(min_length=2, max_length=40)
    min_amount: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=2)
    required_role: str = Field(default="admin", max_length=32)
    is_active: bool = True


class MasterIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    rate: Decimal | None = Field(default=None, ge=0, le=100)
    days_due: int | None = Field(default=None, ge=0)
    symbol: str | None = Field(default=None, max_length=8)
    is_active: bool = True


MASTER_TABLES = {
    "taxes": ("tax_rates", "rate"), "currencies": ("currencies", "symbol"),
    "units": ("units_of_measure", None), "payment-terms": ("payment_terms", "days_due"),
    "cost-centers": ("cost_centers", None),
}


@router.get("/inventory/availability")
def inventory_availability(actor: Actor = Depends(require_permission("inventory:read")), db: Session = Depends(get_db)):
    return db.execute(text("""
        select i.id, i.product_id, i.warehouse_id, i.quantity as on_hand,
               coalesce(sum(r.quantity) filter (where r.status='active'), 0) as reserved_quantity,
               i.quantity - coalesce(sum(r.quantity) filter (where r.status='active'), 0) as available_quantity
        from inventories i left join stock_reservations r on r.tenant_id=i.tenant_id and r.product_id=i.product_id and r.warehouse_id=i.warehouse_id
        where i.tenant_id=:tenant group by i.id, i.product_id, i.warehouse_id, i.quantity order by i.product_id, i.warehouse_id
    """), {"tenant": str(actor.tenant_id)}).mappings().all()


@router.get("/inventory/valuation")
def inventory_valuation(actor: Actor = Depends(require_permission("inventory:read")), db: Session = Depends(get_db)):
    return db.execute(text("""
        select p.id as product_id, p.sku, p.name, p.average_cost, coalesce(sum(i.quantity),0) as quantity,
               coalesce(sum(i.quantity),0) * p.average_cost as inventory_value
        from products p left join inventories i on i.product_id=p.id and i.tenant_id=p.tenant_id
        where p.tenant_id=:tenant group by p.id order by p.name
    """), {"tenant": str(actor.tenant_id)}).mappings().all()


@router.get("/inventory/reservations")
def list_reservations(actor: Actor = Depends(require_permission("inventory:read")), db: Session = Depends(get_db)):
    return db.execute(text("select * from stock_reservations where tenant_id=:tenant order by created_at desc"), {"tenant": str(actor.tenant_id)}).mappings().all()


@router.post("/inventory/reservations", status_code=status.HTTP_201_CREATED)
def create_reservation(data: ReservationIn, actor: Actor = Depends(require_permission("inventory:write")), db: Session = Depends(get_db)):
    # Lock the balance row first.  The prior aggregate-only read allowed two
    # simultaneous requests to observe the same availability and over-reserve.
    stock = db.scalar(select(Inventory).where(
        Inventory.tenant_id == actor.tenant_id,
        Inventory.product_id == data.product_id,
        Inventory.warehouse_id == data.warehouse_id,
    ).with_for_update())
    product = db.scalar(select(Product.id).where(
        Product.id == data.product_id,
        Product.tenant_id == actor.tenant_id,
        Product.is_active.is_(True),
    ))
    warehouse = db.scalar(select(Warehouse.id).where(
        Warehouse.id == data.warehouse_id,
        Warehouse.tenant_id == actor.tenant_id,
        Warehouse.is_active.is_(True),
    ))
    if not product or not warehouse:
        raise HTTPException(status_code=404, detail="Producto o almacén no encontrado en la empresa activa")
    if stock is None:
        raise HTTPException(status_code=422, detail="No hay existencias disponibles suficientes para reservar")
    existing = db.execute(text("""
        select * from stock_reservations
        where tenant_id=:tenant and product_id=:product and warehouse_id=:warehouse
          and reference_type=:type and reference=:reference
          and coalesce(reference_id::text, '')=coalesce(:reference_id, '')
          and status='active'
        for update
    """), {"tenant": str(actor.tenant_id), "product": str(data.product_id), "warehouse": str(data.warehouse_id),
           "type": data.reference_type, "reference": data.reference,
           "reference_id": str(data.reference_id) if data.reference_id else None}).mappings().first()
    if existing:
        if Decimal(str(existing["quantity"])) != data.quantity:
            raise HTTPException(status_code=409, detail="La referencia ya tiene una reserva activa con una cantidad distinta")
        return existing
    reserved = db.execute(text("""
        select coalesce(sum(quantity), 0) from stock_reservations
        where tenant_id=:tenant and product_id=:product and warehouse_id=:warehouse and status='active'
    """), {"tenant": str(actor.tenant_id), "product": str(data.product_id), "warehouse": str(data.warehouse_id)}).scalar() or Decimal("0")
    if stock.quantity - Decimal(str(reserved)) < data.quantity:
        raise HTTPException(status_code=422, detail="No hay existencias disponibles suficientes para reservar")
    row = db.execute(text("""
        insert into stock_reservations(tenant_id,product_id,warehouse_id,reference_type,reference_id,reference,quantity,note,created_by)
        values(:tenant,:product,:warehouse,:type,:reference_id,:reference,:quantity,:note,:actor) returning *
    """), {"tenant": str(actor.tenant_id), "product": str(data.product_id), "warehouse": str(data.warehouse_id), "type": data.reference_type,
           "reference_id": str(data.reference_id) if data.reference_id else None, "reference": data.reference, "quantity": data.quantity, "note": data.note, "actor": str(actor.user_id)}).mappings().one()
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="reserve", module="inventory", entity="stock_reservation", entity_id=row["id"], detail=data.reference)
    db.commit()
    return row


@router.post("/inventory/reservations/{reservation_id}/release")
def release_reservation(reservation_id: UUID, actor: Actor = Depends(require_permission("inventory:write")), db: Session = Depends(get_db)):
    row = db.execute(text("""update stock_reservations set status='released', released_at=now(), updated_at=now()
        where id=:id and tenant_id=:tenant and status='active' returning *"""), {"id": str(reservation_id), "tenant": str(actor.tenant_id)}).mappings().first()
    if not row:
        raise HTTPException(status_code=409, detail="La reserva no existe o ya fue liberada")
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="release", module="inventory", entity="stock_reservation", entity_id=reservation_id)
    db.commit()
    return row


@router.get("/inventory/counts")
def list_counts(actor: Actor = Depends(require_permission("inventory:read")), db: Session = Depends(get_db)):
    return db.execute(text("select * from inventory_counts where tenant_id=:tenant order by created_at desc"), {"tenant": str(actor.tenant_id)}).mappings().all()


@router.post("/inventory/counts", status_code=status.HTTP_201_CREATED)
def create_count(data: CountIn, actor: Actor = Depends(require_permission("inventory:write")), db: Session = Depends(get_db)):
    if len({line.product_id for line in data.lines}) != len(data.lines):
        raise HTTPException(status_code=422, detail="Un producto solo puede aparecer una vez en el inventario físico")
    warehouse_exists = db.execute(text("select 1 from warehouses where id=:warehouse and tenant_id=:tenant and is_active=true"), {"warehouse": str(data.warehouse_id), "tenant": str(actor.tenant_id)}).first()
    if not warehouse_exists:
        raise HTTPException(status_code=404, detail="Almacén no encontrado")
    try:
        count = db.execute(text("""
            insert into inventory_counts(tenant_id,number,warehouse_id,count_date,reason,note,created_by)
            values(:tenant,:number,:warehouse,:date,:reason,:note,:actor) returning *
        """), {"tenant": str(actor.tenant_id), "number": data.number, "warehouse": str(data.warehouse_id), "date": data.count_date,
               "reason": data.reason, "note": data.note, "actor": str(actor.user_id)}).mappings().one()
        for line in data.lines:
            expected = db.execute(text("select coalesce(quantity,0) from inventories where tenant_id=:tenant and product_id=:product and warehouse_id=:warehouse for share"), {"tenant": str(actor.tenant_id), "product": str(line.product_id), "warehouse": str(data.warehouse_id)}).scalar() or 0
            inserted = db.execute(text("""
                insert into inventory_count_lines(tenant_id,inventory_count_id,product_id,expected_quantity,counted_quantity,note)
                select :tenant,:count,:product,:expected,:counted,:note where exists(select 1 from products where id=:product and tenant_id=:tenant and is_active=true)
            """), {"tenant": str(actor.tenant_id), "count": str(count["id"]), "product": str(line.product_id), "expected": expected, "counted": line.counted_quantity, "note": line.note})
            if inserted.rowcount != 1:
                raise HTTPException(status_code=404, detail="Uno de los productos del conteo no pertenece a la empresa activa")
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="create", module="inventory", entity="inventory_count", entity_id=count["id"], detail=data.reason)
        db.commit()
    except Exception as exc:
        db.rollback()
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=409, detail="El número de inventario físico ya existe") from exc
    return count


@router.post("/inventory/counts/{count_id}/submit")
def submit_count(count_id: UUID, actor: Actor = Depends(require_permission("inventory:write")), db: Session = Depends(get_db)):
    row = db.execute(text("update inventory_counts set status='submitted', updated_at=now() where id=:id and tenant_id=:tenant and status='draft' returning *"), {"id": str(count_id), "tenant": str(actor.tenant_id)}).mappings().first()
    if not row:
        raise HTTPException(status_code=409, detail="El conteo no está en borrador")
    policy = db.execute(text("""select id from approval_policies where tenant_id=:tenant and module='inventory' and action='physical_count'
       and is_active=true order by min_amount desc limit 1"""), {"tenant": str(actor.tenant_id)}).scalar()
    request_id = None
    if policy:
        request_id = db.execute(text("""insert into approval_requests(tenant_id,policy_id,module,action,entity_type,entity_id,payload,requested_by)
           values(:tenant,:policy,'inventory','physical_count','inventory_count',:count,jsonb_build_object('number',:number),:actor)
           returning id"""), {"tenant": str(actor.tenant_id), "policy": str(policy), "count": str(count_id), "number": row["number"], "actor": str(actor.user_id)}).scalar_one()
    create_inventory_count_evidence(
        db, tenant_id=actor.tenant_id, count_id=count_id, approval_request_id=request_id, requested_by=actor.user_id,
    )
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="submit", module="inventory", entity="inventory_count", entity_id=count_id)
    db.commit()
    return row


@router.post("/inventory/counts/{count_id}/apply")
def apply_count(count_id: UUID, actor: Actor = Depends(require_permission("inventory:write")), db: Session = Depends(get_db)):
    count = db.execute(text("select * from inventory_counts where id=:id and tenant_id=:tenant for update"), {"id": str(count_id), "tenant": str(actor.tenant_id)}).mappings().first()
    if not count or count["status"] not in {"submitted", "approved"}:
        raise HTTPException(status_code=409, detail="El conteo debe estar enviado o aprobado antes de aplicarse")
    approval = db.execute(text("select status from approval_requests where tenant_id=:tenant and entity_type='inventory_count' and entity_id=:id order by created_at desc limit 1"), {"tenant": str(actor.tenant_id), "id": str(count_id)}).scalar()
    if approval and approval != "approved":
        raise HTTPException(status_code=409, detail="El inventario físico requiere una aprobación antes de aplicarse")
    lines = db.execute(text("select * from inventory_count_lines where inventory_count_id=:id and tenant_id=:tenant order by product_id"), {"id": str(count_id), "tenant": str(actor.tenant_id)}).mappings().all()
    for line in lines:
        # Serialize the reconciliation with every regular stock mutation.  The
        # stock helpers lock this same row, so a receipt/dispatch cannot commit
        # between the point-in-time validation below and the reconciliation.
        # A zero row is intentional for a product counted for the first time:
        # it gives the count a row-level lock even when it had no prior stock.
        db.execute(text("""
            insert into inventories(tenant_id,product_id,warehouse_id,quantity)
            values(:tenant,:product,:warehouse,0)
            on conflict(tenant_id,product_id,warehouse_id) do nothing
        """), {"tenant": str(actor.tenant_id), "product": str(line["product_id"]),
               "warehouse": str(count["warehouse_id"])})
        db.execute(text("""
            select id from inventories
            where tenant_id=:tenant and product_id=:product and warehouse_id=:warehouse
            for update
        """), {"tenant": str(actor.tenant_id), "product": str(line["product_id"]),
               "warehouse": str(count["warehouse_id"])}).scalar_one()
        # A count is a point-in-time document.  Silently replacing a balance
        # after another receipt/dispatch would erase a legitimate movement.
        has_new_movement = db.execute(text("""
            select exists(
              select 1 from inventory_movements
              where tenant_id=:tenant and product_id=:product and warehouse_id=:warehouse
                and created_at > :snapshot_at
            )
        """), {"tenant": str(actor.tenant_id), "product": str(line["product_id"]),
               "warehouse": str(count["warehouse_id"]), "snapshot_at": count["snapshot_at"]}).scalar()
        if has_new_movement:
            raise HTTPException(status_code=409, detail="El stock cambió después del conteo. Crea un nuevo inventario físico para conciliar la diferencia")
        variance = Decimal(str(line["counted_quantity"])) - Decimal(str(line["expected_quantity"]))
        reserved = db.execute(text("""select coalesce(sum(quantity),0) from stock_reservations
            where tenant_id=:tenant and product_id=:product and warehouse_id=:warehouse and status='active'"""), {"tenant": str(actor.tenant_id), "product": str(line["product_id"]), "warehouse": str(count["warehouse_id"])}).scalar() or Decimal("0")
        if Decimal(str(line["counted_quantity"])) < Decimal(str(reserved)):
            raise HTTPException(status_code=422, detail="No se puede aplicar el conteo: la existencia física quedaría por debajo del stock reservado")
        if variance:
            db.execute(text("""
                insert into inventories(tenant_id,product_id,warehouse_id,quantity) values(:tenant,:product,:warehouse,:quantity)
                on conflict(tenant_id,product_id,warehouse_id) do update set quantity=excluded.quantity, updated_at=now()
            """), {"tenant": str(actor.tenant_id), "product": str(line["product_id"]), "warehouse": str(count["warehouse_id"]), "quantity": line["counted_quantity"]})
            db.execute(text("""
                insert into inventory_movements(tenant_id,product_id,warehouse_id,movement_type,quantity,reference,note)
                values(:tenant,:product,:warehouse,'adjustment',:quantity,:reference,:note)
            """), {"tenant": str(actor.tenant_id), "product": str(line["product_id"]), "warehouse": str(count["warehouse_id"]), "quantity": variance,
                   "reference": count["number"], "note": f"Inventario físico: {count['reason']}"})
    row = db.execute(text("update inventory_counts set status='applied', applied_by=:actor, applied_at=now(), updated_at=now() where id=:id returning *"), {"id": str(count_id), "actor": str(actor.user_id)}).mappings().one()
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="apply", module="inventory", entity="inventory_count", entity_id=count_id)
    db.commit()
    return row


@router.get("/approvals/policies")
def approval_policies(actor: Actor = Depends(require_permission("approvals:read")), db: Session = Depends(get_db)):
    return db.execute(text("select * from approval_policies where tenant_id=:tenant order by module, action, min_amount"), {"tenant": str(actor.tenant_id)}).mappings().all()


@router.post("/approvals/policies", status_code=status.HTTP_201_CREATED)
def create_policy(data: PolicyIn, actor: Actor = Depends(require_permission("approvals:write")), db: Session = Depends(get_db)):
    row = db.execute(text("""insert into approval_policies(tenant_id,module,action,min_amount,required_role,is_active)
       values(:tenant,:module,:action,:amount,:role,:active) returning *"""), {"tenant": str(actor.tenant_id), "module": data.module, "action": data.action, "amount": data.min_amount, "role": data.required_role, "active": data.is_active}).mappings().one()
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="create_policy", module="approvals", entity="approval_policy", entity_id=row["id"])
    db.commit(); return row


@router.get("/approvals")
def list_approvals(actor: Actor = Depends(require_permission("approvals:read")), db: Session = Depends(get_db)):
    return db.execute(text("select * from approval_requests where tenant_id=:tenant order by created_at desc"), {"tenant": str(actor.tenant_id)}).mappings().all()


@router.post("/approvals/{request_id}/approve")
def approve_request(request_id: UUID, data: DecisionIn, actor: Actor = Depends(require_permission("approvals:write")), db: Session = Depends(get_db)):
    row = db.execute(text("""update approval_requests r set status='approved', decided_by=:actor, decision_note=:note, decided_at=now(), updated_at=now()
      where r.id=:id and r.tenant_id=:tenant and r.status='pending' and r.requested_by <> :actor
      and (
        (r.policy_id is null and :role in ('owner','admin'))
        or exists(select 1 from approval_policies p where p.id=r.policy_id
          and (p.required_role=:role or :role in ('owner','admin')))
      ) returning r.*"""), {"id": str(request_id), "tenant": str(actor.tenant_id), "actor": str(actor.user_id), "note": data.decision_note, "role": actor.role_code}).mappings().first()
    if not row:
        raise HTTPException(status_code=409, detail="No puede aprobar esta solicitud o ya fue resuelta")
    append_internal_decision_evidence(db, tenant_id=actor.tenant_id, approval_request_id=request_id, signer_user_id=actor.user_id, decision="approved")
    if row["entity_type"] == "purchase_order":
        db.execute(text("""update purchase_orders set status='approved' where id=:id and tenant_id=:tenant and status='submitted'"""), {"id": str(row["entity_id"]), "tenant": str(actor.tenant_id)})
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="approve", module="approvals", entity="approval_request", entity_id=request_id, detail=data.decision_note)
    db.commit(); return row


@router.post("/approvals/{request_id}/reject")
def reject_request(request_id: UUID, data: DecisionIn, actor: Actor = Depends(require_permission("approvals:write")), db: Session = Depends(get_db)):
    row = db.execute(text("""update approval_requests r set status='rejected', decided_by=:actor, decision_note=:note, decided_at=now(), updated_at=now()
      where r.id=:id and r.tenant_id=:tenant and r.status='pending' and r.requested_by <> :actor
      and (
        (r.policy_id is null and :role in ('owner','admin'))
        or exists(select 1 from approval_policies p where p.id=r.policy_id
          and (p.required_role=:role or :role in ('owner','admin')))
      ) returning r.*"""), {"id": str(request_id), "tenant": str(actor.tenant_id), "actor": str(actor.user_id), "note": data.decision_note, "role": actor.role_code}).mappings().first()
    if not row: raise HTTPException(status_code=409, detail="La solicitud no existe o ya fue resuelta")
    append_internal_decision_evidence(db, tenant_id=actor.tenant_id, approval_request_id=request_id, signer_user_id=actor.user_id, decision="rejected")
    if row["entity_type"] == "purchase_order":
        db.execute(text("""update purchase_orders set status='cancelled' where id=:id and tenant_id=:tenant and status='submitted'"""), {"id": str(row["entity_id"]), "tenant": str(actor.tenant_id)})
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="reject", module="approvals", entity="approval_request", entity_id=request_id, detail=data.decision_note)
    db.commit(); return row


@router.get("/finance/cash-flow")
def cash_flow(actor: Actor = Depends(require_permission("treasury:read")), db: Session = Depends(get_db)):
    return db.execute(text("""
      select c.id, c.code, c.name, c.currency,
       coalesce(sum(case when m.movement_type='inflow' then m.amount else -m.amount end),0) as balance,
       coalesce(sum(case when m.movement_type='inflow' and m.created_at >= date_trunc('month',now()) then m.amount else 0 end),0) as month_inflow,
       coalesce(sum(case when m.movement_type='outflow' and m.created_at >= date_trunc('month',now()) then m.amount else 0 end),0) as month_outflow
      from cash_accounts c left join cash_movements m on m.cash_account_id=c.id and m.tenant_id=c.tenant_id
      where c.tenant_id=:tenant group by c.id,c.code,c.name,c.currency order by c.name
    """), {"tenant": str(actor.tenant_id)}).mappings().all()


@router.get("/finance/aging")
def aging(actor: Actor = Depends(require_permission("accounts:read")), db: Session = Depends(get_db)):
    return db.execute(text("""
      select 'receivable' as kind, id, customer_id as counterpart_id, due_date, balance_amount,
       greatest(current_date-due_date,0) as overdue_days, case when due_date < current_date then 'overdue' else 'current' end as aging_status from accounts_receivable where tenant_id=:tenant and status in ('open','partial')
      union all
      select 'payable', id, supplier_id, due_date, balance_amount, greatest(current_date-due_date,0), case when due_date < current_date then 'overdue' else 'current' end from accounts_payable where tenant_id=:tenant and status in ('open','partial')
      order by due_date
    """), {"tenant": str(actor.tenant_id)}).mappings().all()


@router.get("/accounting/journal")
def journal(actor: Actor = Depends(require_permission("accounting:read")), db: Session = Depends(get_db)):
    return db.execute(text("""select e.*, coalesce(sum(l.debit),0) as debit, coalesce(sum(l.credit),0) as credit
      from journal_entries e left join journal_lines l on l.journal_entry_id=e.id where e.tenant_id=:tenant
      group by e.id order by e.entry_date desc,e.created_at desc"""), {"tenant": str(actor.tenant_id)}).mappings().all()


@router.get("/accounting/trial-balance")
def trial_balance(actor: Actor = Depends(require_permission("accounting:read")), db: Session = Depends(get_db)):
    return db.execute(text("""select a.code,a.name,a.account_type,coalesce(sum(l.debit),0) as debit,coalesce(sum(l.credit),0) as credit,
      coalesce(sum(l.debit-l.credit),0) as balance from chart_of_accounts a left join journal_lines l on l.account_id=a.id and l.tenant_id=a.tenant_id
      where a.tenant_id=:tenant group by a.id order by a.code"""), {"tenant": str(actor.tenant_id)}).mappings().all()


@router.post("/configuration/{resource}", status_code=status.HTTP_201_CREATED)
def create_master(resource: str, data: MasterIn, actor: Actor = Depends(require_permission("configuration:write")), db: Session = Depends(get_db)):
    definition = MASTER_TABLES.get(resource)
    if not definition: raise HTTPException(status_code=404, detail="Catálogo no reconocido")
    table, extra = definition
    values = {"tenant": str(actor.tenant_id), "code": data.code.upper(), "name": data.name, "active": data.is_active}
    columns, params = "tenant_id,code,name,is_active", ":tenant,:code,:name,:active"
    if extra:
        value = getattr(data, extra)
        if value is None: raise HTTPException(status_code=422, detail=f"El campo {extra} es obligatorio")
        values[extra] = value; columns += f",{extra}"; params += f",:{extra}"
    try:
        row = db.execute(text(f"insert into {table}({columns}) values({params}) returning *"), values).mappings().one()
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="create", module="configuration", entity=table, entity_id=row["id"])
        db.commit(); return row
    except Exception as exc:
        db.rollback(); raise HTTPException(status_code=409, detail="El código ya existe en este catálogo") from exc


@router.get("/configuration/{resource}")
def list_master(resource: str, actor: Actor = Depends(require_permission("configuration:read")), db: Session = Depends(get_db)):
    definition = MASTER_TABLES.get(resource)
    if not definition: raise HTTPException(status_code=404, detail="Catálogo no reconocido")
    return db.execute(text(f"select * from {definition[0]} where tenant_id=:tenant order by code"), {"tenant": str(actor.tenant_id)}).mappings().all()
