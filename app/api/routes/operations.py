from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import Inventory, Product, Warehouse
from app.security.auth import Actor, require_permission
from app.services.audit import record
from app.services.approval_documents import append_internal_decision_evidence, create_inventory_count_evidence
from app.services.internal_signatures import (
    jwk_fingerprint,
    message_hash,
    signing_message,
    validate_p256_jwk,
    verify_p256_signature,
)
from app.services.idempotency import claim as claim_idempotency, complete as complete_idempotency, replay_response

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
    challenge_id: UUID
    signing_key_id: UUID
    signature: str = Field(min_length=80, max_length=512)


class SigningKeyIn(BaseModel):
    label: str = Field(default="Este dispositivo", min_length=1, max_length=120)
    public_jwk: dict[str, str]


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
    valid_from: date | None = None
    valid_to: date | None = None
    is_default: bool = False
    is_active: bool = True

    @field_validator("valid_to")
    @classmethod
    def master_valid_to_is_not_before_valid_from(cls, value: date | None, info) -> date | None:
        valid_from = info.data.get("valid_from")
        if value and valid_from and value < valid_from:
            raise ValueError("La vigencia final no puede ser anterior a la inicial")
        return value


class ExchangeRateIn(BaseModel):
    currency_id: UUID
    effective_date: date = Field(default_factory=date.today)
    rate_to_base: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    source: str = Field(default="manual", min_length=2, max_length=40)


class PriceListIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    currency_id: UUID
    valid_from: date = Field(default_factory=date.today)
    valid_to: date | None = None
    is_active: bool = True

    @field_validator("valid_to")
    @classmethod
    def valid_end_is_not_past_start(cls, value: date | None, info) -> date | None:
        if value and value < info.data.get("valid_from", value):
            raise ValueError("La vigencia final no puede ser anterior a la inicial")
        return value


class PriceListItemIn(BaseModel):
    product_id: UUID
    min_quantity: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=3)
    unit_price: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    valid_from: date = Field(default_factory=date.today)
    valid_to: date | None = None
    is_active: bool = True

    @field_validator("valid_to")
    @classmethod
    def item_valid_end_is_not_past_start(cls, value: date | None, info) -> date | None:
        if value and value < info.data.get("valid_from", value):
            raise ValueError("La vigencia final no puede ser anterior a la inicial")
        return value


class DiscountPolicyIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    scope: str = Field(default="sale", pattern="^(sale|customer|product|price_list)$")
    max_discount_percent: Decimal = Field(ge=0, le=100, max_digits=7, decimal_places=4)
    requires_approval: bool = False
    valid_from: date = Field(default_factory=date.today)
    valid_to: date | None = None
    is_active: bool = True

    @field_validator("valid_to")
    @classmethod
    def policy_valid_end_is_not_past_start(cls, value: date | None, info) -> date | None:
        if value and value < info.data.get("valid_from", value):
            raise ValueError("La vigencia final no puede ser anterior a la inicial")
        return value


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
def create_reservation(request: Request, data: ReservationIn, actor: Actor = Depends(require_permission("inventory:write")), db: Session = Depends(get_db)):
    idempotency = claim_idempotency(
        db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id,
        operation="inventory.reservations.create", key=request.headers.get("Idempotency-Key"),
        payload=data.model_dump(mode="json"),
    )
    if idempotency and idempotency.is_replay:
        return replay_response(idempotency)
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
        complete_idempotency(db, claim=idempotency, response_body=dict(existing), response_status=status.HTTP_201_CREATED)
        db.commit()
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
    complete_idempotency(db, claim=idempotency, response_body=dict(row), response_status=status.HTTP_201_CREATED)
    db.commit()
    return row


@router.post("/inventory/reservations/{reservation_id}/release")
def release_reservation(reservation_id: UUID, request: Request, actor: Actor = Depends(require_permission("inventory:write")), db: Session = Depends(get_db)):
    idempotency = claim_idempotency(
        db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id,
        operation="inventory.reservations.release", key=request.headers.get("Idempotency-Key"),
        payload={"reservation_id": str(reservation_id)},
    )
    if idempotency and idempotency.is_replay:
        return replay_response(idempotency)
    row = db.execute(text("""update stock_reservations set status='released', released_at=now(), updated_at=now()
        where id=:id and tenant_id=:tenant and status='active' returning *"""), {"id": str(reservation_id), "tenant": str(actor.tenant_id)}).mappings().first()
    if not row:
        raise HTTPException(status_code=409, detail="La reserva no existe o ya fue liberada")
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="release", module="inventory", entity="stock_reservation", entity_id=reservation_id)
    complete_idempotency(db, claim=idempotency, response_body=dict(row), response_status=status.HTTP_200_OK)
    db.commit()
    return row


@router.get("/inventory/counts")
def list_counts(actor: Actor = Depends(require_permission("inventory:read")), db: Session = Depends(get_db)):
    return db.execute(text("select * from inventory_counts where tenant_id=:tenant order by created_at desc"), {"tenant": str(actor.tenant_id)}).mappings().all()


@router.post("/inventory/counts", status_code=status.HTTP_201_CREATED)
def create_count(request: Request, data: CountIn, actor: Actor = Depends(require_permission("inventory:write")), db: Session = Depends(get_db)):
    idempotency = claim_idempotency(
        db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id,
        operation="inventory.counts.create", key=request.headers.get("Idempotency-Key"),
        payload=data.model_dump(mode="json"),
    )
    if idempotency and idempotency.is_replay:
        return replay_response(idempotency)
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
        complete_idempotency(db, claim=idempotency, response_body=dict(count), response_status=status.HTTP_201_CREATED)
        db.commit()
    except Exception as exc:
        db.rollback()
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=409, detail="El número de inventario físico ya existe") from exc
    return count


@router.post("/inventory/counts/{count_id}/submit")
def submit_count(count_id: UUID, request: Request, actor: Actor = Depends(require_permission("inventory:write")), db: Session = Depends(get_db)):
    idempotency = claim_idempotency(
        db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id,
        operation="inventory.counts.submit", key=request.headers.get("Idempotency-Key"),
        payload={"count_id": str(count_id)},
    )
    if idempotency and idempotency.is_replay:
        return replay_response(idempotency)
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
    complete_idempotency(db, claim=idempotency, response_body=dict(row), response_status=status.HTTP_200_OK)
    db.commit()
    return row


@router.post("/inventory/counts/{count_id}/apply")
def apply_count(count_id: UUID, request: Request, actor: Actor = Depends(require_permission("inventory:write")), db: Session = Depends(get_db)):
    idempotency = claim_idempotency(
        db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id,
        operation="inventory.counts.apply", key=request.headers.get("Idempotency-Key"),
        payload={"count_id": str(count_id)},
    )
    if idempotency and idempotency.is_replay:
        return replay_response(idempotency)
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
    row = db.execute(text("""
        update inventory_counts
        set status='applied', applied_by=:actor, applied_at=now(), updated_at=now()
        where id=:id and tenant_id=:tenant and status in ('submitted', 'approved')
        returning *
    """), {
        "id": str(count_id), "tenant": str(actor.tenant_id), "actor": str(actor.user_id),
    }).mappings().one()
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="apply", module="inventory", entity="inventory_count", entity_id=count_id)
    complete_idempotency(db, claim=idempotency, response_body=dict(row), response_status=status.HTTP_200_OK)
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


@router.post("/approvals/signing-keys", status_code=status.HTTP_201_CREATED)
def register_signing_key(data: SigningKeyIn, actor: Actor = Depends(require_permission("approvals:write")), db: Session = Depends(get_db)):
    """Register only a public browser key; the private key never reaches the ERP."""
    try:
        validate_p256_jwk(data.public_jwk)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row = db.execute(text("""
        insert into user_signing_keys(tenant_id,user_id,label,public_jwk,fingerprint)
        values(:tenant,:user,:label,cast(:jwk as jsonb),:fingerprint)
        on conflict(tenant_id,user_id,fingerprint) do update set label=excluded.label, is_active=true, revoked_at=null
        returning id, label, algorithm, fingerprint, created_at
    """), {"tenant": str(actor.tenant_id), "user": str(actor.user_id), "label": data.label,
           "jwk": __import__("json").dumps(data.public_jwk, sort_keys=True), "fingerprint": jwk_fingerprint(data.public_jwk)}).mappings().one()
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="register_signing_key", module="approvals", entity="user_signing_key", entity_id=row["id"])
    db.commit()
    return row


@router.post("/approvals/{request_id}/signature-challenge")
def create_signature_challenge(request_id: UUID, decision: str, actor: Actor = Depends(require_permission("approvals:write")), db: Session = Depends(get_db)):
    if decision not in {"approved", "rejected"}:
        raise HTTPException(status_code=422, detail="Decisión de firma no válida")
    row = db.execute(text("""
        select r.id as request_id, d.id as document_id, d.content_hash
        from approval_requests r
        join approval_documents d on d.approval_request_id=r.id and d.tenant_id=r.tenant_id
        where r.id=:id and r.tenant_id=:tenant and r.status='pending' and r.requested_by <> :actor
          and ((r.policy_id is null and :role in ('owner','admin'))
            or exists(select 1 from approval_policies p where p.id=r.policy_id
              and (p.required_role=:role or :role in ('owner','admin'))))
        order by d.created_at desc limit 1
    """), {"id": str(request_id), "tenant": str(actor.tenant_id), "actor": str(actor.user_id), "role": actor.role_code}).mappings().first()
    if not row:
        raise HTTPException(status_code=409, detail="No puede firmar esta solicitud o no tiene una constancia disponible")
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    challenge = db.execute(text("""
        insert into approval_signature_challenges(tenant_id,approval_request_id,approval_document_id,signer_user_id,decision,expires_at)
        values(:tenant,:request,:document,:signer,:decision,:expires) returning id, expires_at
    """), {"tenant": str(actor.tenant_id), "request": str(request_id), "document": str(row["document_id"]),
           "signer": str(actor.user_id), "decision": decision, "expires": expires_at}).mappings().one()
    message = signing_message(challenge_id=challenge["id"], document_id=row["document_id"], document_hash=row["content_hash"], decision=decision, signer_user_id=actor.user_id)
    db.commit()
    return {"challenge_id": challenge["id"], "document_id": row["document_id"], "document_hash": row["content_hash"], "message": message, "expires_at": challenge["expires_at"], "algorithm": "ECDSA_P256_SHA256"}


def _verify_decision_signature(request_id: UUID, data: DecisionIn, decision: str, actor: Actor, db: Session) -> dict:
    challenge = db.execute(text("""
        select c.*, d.content_hash, k.public_jwk
        from approval_signature_challenges c
        join approval_documents d on d.id=c.approval_document_id and d.tenant_id=c.tenant_id
        join user_signing_keys k on k.id=:key and k.tenant_id=c.tenant_id and k.user_id=c.signer_user_id and k.is_active=true
        where c.id=:challenge and c.approval_request_id=:request and c.tenant_id=:tenant
          and c.signer_user_id=:actor and c.decision=:decision and c.used_at is null and c.expires_at > now()
        for update
    """), {"challenge": str(data.challenge_id), "request": str(request_id), "tenant": str(actor.tenant_id),
           "actor": str(actor.user_id), "decision": decision, "key": str(data.signing_key_id)}).mappings().first()
    if not challenge:
        raise HTTPException(status_code=409, detail="La solicitud de firma expiró, fue usada o no corresponde a este documento")
    message = signing_message(challenge_id=challenge["id"], document_id=challenge["approval_document_id"], document_hash=challenge["content_hash"], decision=decision, signer_user_id=actor.user_id)
    try:
        verify_p256_signature(public_jwk=challenge["public_jwk"], message=message, signature_b64=data.signature)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.execute(text("""
        update approval_signature_challenges
        set used_at=now()
        where id=:id and tenant_id=:tenant and used_at is null
    """), {"id": str(challenge["id"]), "tenant": str(actor.tenant_id)})
    return {"signed_hash": message_hash(message), "signing_key_id": data.signing_key_id, "signature_value": data.signature}


@router.post("/approvals/{request_id}/approve")
def approve_request(request_id: UUID, data: DecisionIn, actor: Actor = Depends(require_permission("approvals:write")), db: Session = Depends(get_db)):
    signature = _verify_decision_signature(request_id, data, "approved", actor, db)
    row = db.execute(text("""update approval_requests r set status='approved', decided_by=:actor, decision_note=:note, decided_at=now(), updated_at=now()
      where r.id=:id and r.tenant_id=:tenant and r.status='pending' and r.requested_by <> :actor
      and (
        (r.policy_id is null and :role in ('owner','admin'))
        or exists(select 1 from approval_policies p where p.id=r.policy_id
          and (p.required_role=:role or :role in ('owner','admin')))
      ) returning r.*"""), {"id": str(request_id), "tenant": str(actor.tenant_id), "actor": str(actor.user_id), "note": data.decision_note, "role": actor.role_code}).mappings().first()
    if not row:
        raise HTTPException(status_code=409, detail="No puede aprobar esta solicitud o ya fue resuelta")
    append_internal_decision_evidence(db, tenant_id=actor.tenant_id, approval_request_id=request_id, signer_user_id=actor.user_id, decision="approved", **signature)
    if row["entity_type"] == "purchase_order":
        db.execute(text("""update purchase_orders set status='approved' where id=:id and tenant_id=:tenant and status='submitted'"""), {"id": str(row["entity_id"]), "tenant": str(actor.tenant_id)})
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="approve", module="approvals", entity="approval_request", entity_id=request_id, detail=data.decision_note)
    db.commit(); return row


@router.post("/approvals/{request_id}/reject")
def reject_request(request_id: UUID, data: DecisionIn, actor: Actor = Depends(require_permission("approvals:write")), db: Session = Depends(get_db)):
    signature = _verify_decision_signature(request_id, data, "rejected", actor, db)
    row = db.execute(text("""update approval_requests r set status='rejected', decided_by=:actor, decision_note=:note, decided_at=now(), updated_at=now()
      where r.id=:id and r.tenant_id=:tenant and r.status='pending' and r.requested_by <> :actor
      and (
        (r.policy_id is null and :role in ('owner','admin'))
        or exists(select 1 from approval_policies p where p.id=r.policy_id
          and (p.required_role=:role or :role in ('owner','admin')))
      ) returning r.*"""), {"id": str(request_id), "tenant": str(actor.tenant_id), "actor": str(actor.user_id), "note": data.decision_note, "role": actor.role_code}).mappings().first()
    if not row: raise HTTPException(status_code=409, detail="La solicitud no existe o ya fue resuelta")
    append_internal_decision_evidence(db, tenant_id=actor.tenant_id, approval_request_id=request_id, signer_user_id=actor.user_id, decision="rejected", **signature)
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


def _ensure_active_currency(db: Session, tenant_id: UUID, currency_id: UUID) -> None:
    exists = db.execute(text("""
        select 1 from currencies
        where id=:currency and tenant_id=:tenant and is_active=true
    """), {"currency": str(currency_id), "tenant": str(tenant_id)}).first()
    if not exists:
        raise HTTPException(status_code=422, detail="La moneda debe estar activa y pertenecer a la empresa")


@router.get("/configuration/exchange-rates")
def list_exchange_rates(actor: Actor = Depends(require_permission("configuration:read")), db: Session = Depends(get_db)):
    return db.execute(text("""
        select r.*, c.code as currency_code, c.name as currency_name, c.symbol as currency_symbol
        from currency_exchange_rates r
        join currencies c on c.id=r.currency_id and c.tenant_id=r.tenant_id
        where r.tenant_id=:tenant
        order by r.effective_date desc, c.code
    """), {"tenant": str(actor.tenant_id)}).mappings().all()


@router.post("/configuration/exchange-rates", status_code=status.HTTP_201_CREATED)
def create_exchange_rate(data: ExchangeRateIn, actor: Actor = Depends(require_permission("configuration:write")), db: Session = Depends(get_db)):
    _ensure_active_currency(db, actor.tenant_id, data.currency_id)
    try:
        row = db.execute(text("""
            insert into currency_exchange_rates(tenant_id,currency_id,effective_date,rate_to_base,source)
            values(:tenant,:currency,:effective_date,:rate,:source) returning *
        """), {"tenant": str(actor.tenant_id), "currency": str(data.currency_id),
               "effective_date": data.effective_date, "rate": data.rate_to_base,
               "source": data.source.strip()}).mappings().one()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ya existe un tipo de cambio para esa moneda y fecha") from exc
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="create",
           module="configuration", entity="currency_exchange_rate", entity_id=row["id"])
    db.commit()
    return row


@router.get("/configuration/price-lists")
def list_price_lists(actor: Actor = Depends(require_permission("configuration:read")), db: Session = Depends(get_db)):
    return db.execute(text("""
        select l.*, c.code as currency_code, c.symbol as currency_symbol,
               count(i.id) as item_count
        from price_lists l
        join currencies c on c.id=l.currency_id and c.tenant_id=l.tenant_id
        left join price_list_items i on i.price_list_id=l.id and i.tenant_id=l.tenant_id and i.is_active=true
        where l.tenant_id=:tenant
        group by l.id,c.code,c.symbol
        order by l.is_active desc,l.valid_from desc,l.code
    """), {"tenant": str(actor.tenant_id)}).mappings().all()


@router.post("/configuration/price-lists", status_code=status.HTTP_201_CREATED)
def create_price_list(data: PriceListIn, actor: Actor = Depends(require_permission("configuration:write")), db: Session = Depends(get_db)):
    _ensure_active_currency(db, actor.tenant_id, data.currency_id)
    try:
        row = db.execute(text("""
            insert into price_lists(tenant_id,code,name,currency_id,valid_from,valid_to,is_active)
            values(:tenant,:code,:name,:currency,:valid_from,:valid_to,:active) returning *
        """), {"tenant": str(actor.tenant_id), "code": data.code.strip().upper(), "name": data.name.strip(),
               "currency": str(data.currency_id), "valid_from": data.valid_from,
               "valid_to": data.valid_to, "active": data.is_active}).mappings().one()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El código de lista de precios ya existe") from exc
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="create",
           module="configuration", entity="price_list", entity_id=row["id"])
    db.commit()
    return row


@router.get("/configuration/price-lists/{price_list_id}/items")
def list_price_list_items(price_list_id: UUID, actor: Actor = Depends(require_permission("configuration:read")), db: Session = Depends(get_db)):
    return db.execute(text("""
        select i.*, p.sku as product_sku, p.name as product_name
        from price_list_items i
        join products p on p.id=i.product_id and p.tenant_id=i.tenant_id
        where i.tenant_id=:tenant and i.price_list_id=:price_list
        order by i.is_active desc,p.name,i.min_quantity,i.valid_from desc
    """), {"tenant": str(actor.tenant_id), "price_list": str(price_list_id)}).mappings().all()


@router.post("/configuration/price-lists/{price_list_id}/items", status_code=status.HTTP_201_CREATED)
def create_price_list_item(price_list_id: UUID, data: PriceListItemIn, actor: Actor = Depends(require_permission("configuration:write")), db: Session = Depends(get_db)):
    parent = db.execute(text("""
        select id from price_lists
        where id=:price_list and tenant_id=:tenant and is_active=true
    """), {"price_list": str(price_list_id), "tenant": str(actor.tenant_id)}).first()
    product = db.execute(text("""
        select id from products where id=:product and tenant_id=:tenant and is_active=true
    """), {"product": str(data.product_id), "tenant": str(actor.tenant_id)}).first()
    if not parent or not product:
        raise HTTPException(status_code=422, detail="La lista y el producto deben estar activos y pertenecer a la empresa")
    try:
        row = db.execute(text("""
            insert into price_list_items(tenant_id,price_list_id,product_id,min_quantity,unit_price,valid_from,valid_to,is_active)
            values(:tenant,:price_list,:product,:min_quantity,:unit_price,:valid_from,:valid_to,:active)
            returning *
        """), {"tenant": str(actor.tenant_id), "price_list": str(price_list_id), "product": str(data.product_id),
               "min_quantity": data.min_quantity, "unit_price": data.unit_price, "valid_from": data.valid_from,
               "valid_to": data.valid_to, "active": data.is_active}).mappings().one()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ya existe una vigencia para ese producto y cantidad mínima") from exc
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="create",
           module="configuration", entity="price_list_item", entity_id=row["id"])
    db.commit()
    return row


@router.get("/configuration/discount-policies")
def list_discount_policies(actor: Actor = Depends(require_permission("configuration:read")), db: Session = Depends(get_db)):
    return db.execute(text("""
        select * from discount_policies where tenant_id=:tenant
        order by is_active desc,valid_from desc,code
    """), {"tenant": str(actor.tenant_id)}).mappings().all()


@router.post("/configuration/discount-policies", status_code=status.HTTP_201_CREATED)
def create_discount_policy(data: DiscountPolicyIn, actor: Actor = Depends(require_permission("configuration:write")), db: Session = Depends(get_db)):
    try:
        row = db.execute(text("""
            insert into discount_policies(
                tenant_id,code,name,scope,max_discount_percent,requires_approval,valid_from,valid_to,is_active
            ) values(
                :tenant,:code,:name,:scope,:max_discount_percent,:requires_approval,:valid_from,:valid_to,:active
            ) returning *
        """), {"tenant": str(actor.tenant_id), "code": data.code.strip().upper(), "name": data.name.strip(),
               "scope": data.scope, "max_discount_percent": data.max_discount_percent,
               "requires_approval": data.requires_approval, "valid_from": data.valid_from,
               "valid_to": data.valid_to, "active": data.is_active}).mappings().one()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El código de política de descuento ya existe") from exc
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="create",
           module="configuration", entity="discount_policy", entity_id=row["id"])
    db.commit()
    return row


@router.get("/configuration/{resource}")
def list_master(resource: str, actor: Actor = Depends(require_permission("configuration:read")), db: Session = Depends(get_db)):
    definition = MASTER_TABLES.get(resource)
    if not definition:
        raise HTTPException(status_code=404, detail="Catálogo no reconocido")
    return db.execute(
        text(f"select * from {definition[0]} where tenant_id=:tenant order by code"),
        {"tenant": str(actor.tenant_id)},
    ).mappings().all()


@router.post("/configuration/{resource}", status_code=status.HTTP_201_CREATED)
def create_master(resource: str, data: MasterIn, actor: Actor = Depends(require_permission("configuration:write")), db: Session = Depends(get_db)):
    definition = MASTER_TABLES.get(resource)
    if not definition:
        raise HTTPException(status_code=404, detail="Catálogo no reconocido")
    table, extra = definition
    values = {"tenant": str(actor.tenant_id), "code": data.code.strip().upper(), "name": data.name.strip(), "active": data.is_active}
    columns, params = "tenant_id,code,name,is_active", ":tenant,:code,:name,:active"
    if extra:
        value = getattr(data, extra)
        if value is None:
            raise HTTPException(status_code=422, detail=f"El campo {extra} es obligatorio")
        values[extra] = value
        columns += f",{extra}"
        params += f",:{extra}"
    if resource == "taxes":
        values["valid_from"] = data.valid_from or date.today()
        values["valid_to"] = data.valid_to
        values["is_default"] = data.is_default
        columns += ",valid_from,valid_to,is_default"
        params += ",:valid_from,:valid_to,:is_default"
    try:
        if resource == "taxes" and data.is_default:
            # A tenant can have one effective default tax for a given date.
            # Older defaults remain as historical records, ending the day before.
            conflict = db.execute(text("""
                select 1 from tax_rates
                where tenant_id=:tenant and is_default=true and is_active=true
                  and valid_from=:valid_from
                limit 1
            """), {"tenant": str(actor.tenant_id), "valid_from": values["valid_from"]}).first()
            if conflict:
                raise HTTPException(status_code=409, detail="Ya existe una tasa predeterminada que inicia en esa fecha")
            db.execute(text("""
                update tax_rates
                set valid_to = :end_date
                where tenant_id=:tenant and is_default=true and is_active=true
                  and valid_from < :valid_from
                  and (valid_to is null or valid_to >= :valid_from)
            """), {
                "tenant": str(actor.tenant_id),
                "valid_from": values["valid_from"],
                "end_date": values["valid_from"] - timedelta(days=1),
            })
        row = db.execute(text(f"insert into {table}({columns}) values({params}) returning *"), values).mappings().one()
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="create", module="configuration", entity=table, entity_id=row["id"])
        db.commit()
        return row
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El código ya existe en este catálogo") from exc
