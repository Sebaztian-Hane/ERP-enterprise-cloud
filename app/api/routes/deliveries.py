"""Sales fulfilment: draft delivery -> dispatch -> proof of delivery.

The delivery is the stock-owning side of a sales order.  It deliberately does
not issue a fiscal document: that remains a separate, auditable sales action.
"""

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import Inventory, Product
from app.schemas.deliveries import SalesDeliveryCreate, SalesDeliveryDispatch
from app.security.auth import Actor, require_module, require_permission
from app.services.audit import record
from app.services.idempotency import claim as claim_idempotency, complete as complete_idempotency, replay_response
from app.services.traceability import issue_tracked_stock


router = APIRouter(prefix="/sales-deliveries", tags=["sales deliveries"])


def _delivery_payload(db: Session, *, tenant_id: UUID, delivery_id: UUID) -> dict:
    header = db.execute(text("""
        select d.*, o.number as sales_order_number, c.name as customer_name
        from sales_deliveries d
        join sales_orders o on o.id=d.sales_order_id and o.tenant_id=d.tenant_id
        join customers c on c.id=o.customer_id and c.tenant_id=o.tenant_id
        where d.id=:id and d.tenant_id=:tenant
    """), {"id": str(delivery_id), "tenant": str(tenant_id)}).mappings().first()
    if not header:
        raise HTTPException(status_code=404, detail="Despacho no encontrado")
    lines = db.execute(text("""
        select i.product_id, p.sku, p.name as product_name, i.quantity
        from sales_delivery_items i
        join products p on p.id=i.product_id and p.tenant_id=i.tenant_id
        where i.delivery_id=:id and i.tenant_id=:tenant
        order by p.name, i.id
    """), {"id": str(delivery_id), "tenant": str(tenant_id)}).mappings().all()
    return {**dict(header), "lines": [dict(line) for line in lines]}


@router.get("")
def list_sales_deliveries(
    actor: Actor = Depends(require_permission("sales:read", "sales_orders")),
    db: Session = Depends(get_db),
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=100),
):
    statement = text("""
        select d.*, o.number as sales_order_number, c.name as customer_name
        from sales_deliveries d
        join sales_orders o on o.id=d.sales_order_id and o.tenant_id=d.tenant_id
        join customers c on c.id=o.customer_id and c.tenant_id=o.tenant_id
        where d.tenant_id=:tenant order by d.created_at desc, d.id
    """).bindparams(tenant=str(actor.tenant_id))
    if page is None and page_size is None:
        return db.execute(statement).mappings().all()
    # Pagination helper uses SQLAlchemy Select rather than raw SQL. Keep the
    # external response contract consistent by serving a bounded page here.
    offset = ((page or 1) - 1) * (page_size or 25)
    rows = db.execute(text("""
        select d.*, o.number as sales_order_number, c.name as customer_name
        from sales_deliveries d
        join sales_orders o on o.id=d.sales_order_id and o.tenant_id=d.tenant_id
        join customers c on c.id=o.customer_id and c.tenant_id=o.tenant_id
        where d.tenant_id=:tenant order by d.created_at desc, d.id
        limit :limit offset :offset
    """), {"tenant": str(actor.tenant_id), "limit": page_size or 25, "offset": offset}).mappings().all()
    total = db.execute(text("select count(*) from sales_deliveries where tenant_id=:tenant"), {"tenant": str(actor.tenant_id)}).scalar_one()
    actual_page, actual_size = page or 1, page_size or 25
    return {"deliveries": rows, "meta": {"page": actual_page, "page_size": actual_size, "total": total, "total_pages": (total + actual_size - 1) // actual_size}}


@router.get("/{delivery_id}")
def get_sales_delivery(delivery_id: UUID, actor: Actor = Depends(require_permission("sales:read", "sales_orders")), db: Session = Depends(get_db)):
    return _delivery_payload(db, tenant_id=actor.tenant_id, delivery_id=delivery_id)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_sales_delivery(request: Request, data: SalesDeliveryCreate, actor: Actor = Depends(require_permission("sales:write")), _: Actor = Depends(require_module("sales_orders")), db: Session = Depends(get_db)):
    idempotency = claim_idempotency(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id,
        operation="sales.deliveries.create", key=request.headers.get("Idempotency-Key"), payload=data.model_dump(mode="json"))
    if idempotency and idempotency.is_replay:
        return replay_response(idempotency)
    order = db.execute(text("""
        select * from sales_orders where id=:id and tenant_id=:tenant and status='confirmed' for update
    """), {"id": str(data.sales_order_id), "tenant": str(actor.tenant_id)}).mappings().first()
    if not order:
        raise HTTPException(status_code=409, detail="El pedido debe estar confirmado y pendiente de despacho")
    order_lines = {row["product_id"]: Decimal(str(row["quantity"])) for row in db.execute(text("""
        select product_id, quantity from sales_order_items where order_id=:id and tenant_id=:tenant
    """), {"id": str(data.sales_order_id), "tenant": str(actor.tenant_id)}).mappings().all()}
    if not order_lines:
        raise HTTPException(status_code=422, detail="El pedido no tiene líneas")
    requested = {line.product_id: line for line in data.lines}
    if not set(requested).issubset(order_lines):
        raise HTTPException(status_code=422, detail="El despacho contiene un producto que no pertenece al pedido")
    planned_rows = db.execute(text("""
        select i.product_id, coalesce(sum(i.quantity), 0) as quantity
        from sales_delivery_items i
        join sales_deliveries d on d.id=i.delivery_id and d.tenant_id=i.tenant_id
        where d.tenant_id=:tenant and d.sales_order_id=:order and d.status in ('draft','dispatched','delivered')
        group by i.product_id
    """), {"tenant": str(actor.tenant_id), "order": str(data.sales_order_id)}).mappings().all()
    planned = {row["product_id"]: Decimal(str(row["quantity"])) for row in planned_rows}
    for product_id, line in requested.items():
        if planned.get(product_id, Decimal("0")) + line.quantity > order_lines[product_id]:
            raise HTTPException(status_code=422, detail="La cantidad planificada supera el saldo pendiente del pedido")
    try:
        delivery_id = db.execute(text("""
            insert into sales_deliveries(tenant_id,sales_order_id,number,warehouse_id,recipient_name,recipient_document,note,created_by)
            values(:tenant,:order,:number,:warehouse,:recipient_name,:recipient_document,:note,:actor) returning id
        """), {"tenant": str(actor.tenant_id), "order": str(data.sales_order_id), "number": data.number.strip(),
               "warehouse": str(order["warehouse_id"]), "recipient_name": data.recipient_name, "recipient_document": data.recipient_document,
               "note": data.note, "actor": str(actor.user_id)}).scalar_one()
        for line in data.lines:
            db.execute(text("""insert into sales_delivery_items(tenant_id,delivery_id,product_id,quantity)
                values(:tenant,:delivery,:product,:quantity)"""), {"tenant": str(actor.tenant_id), "delivery": str(delivery_id), "product": str(line.product_id), "quantity": line.quantity})
        result = _delivery_payload(db, tenant_id=actor.tenant_id, delivery_id=delivery_id)
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="create", module="sales", entity="sales_delivery", entity_id=delivery_id, detail=data.number)
        complete_idempotency(db, claim=idempotency, response_body=result, response_status=status.HTTP_201_CREATED)
        db.commit()
        return result
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El número de despacho ya existe en esta empresa") from exc


@router.post("/{delivery_id}/dispatch")
def dispatch_sales_delivery(delivery_id: UUID, data: SalesDeliveryDispatch, request: Request, actor: Actor = Depends(require_permission("inventory:write")), _: Actor = Depends(require_module("sales_orders")), db: Session = Depends(get_db)):
    idempotency = claim_idempotency(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id,
        operation="sales.deliveries.dispatch", key=request.headers.get("Idempotency-Key"), payload={"delivery_id": str(delivery_id), **data.model_dump(mode="json")})
    if idempotency and idempotency.is_replay:
        return replay_response(idempotency)
    delivery = db.execute(text("""select * from sales_deliveries where id=:id and tenant_id=:tenant and status='draft' for update"""), {"id": str(delivery_id), "tenant": str(actor.tenant_id)}).mappings().first()
    if not delivery:
        raise HTTPException(status_code=409, detail="El despacho debe estar en borrador")
    order = db.execute(text("""select * from sales_orders where id=:id and tenant_id=:tenant and status='confirmed' for update"""), {"id": str(delivery["sales_order_id"]), "tenant": str(actor.tenant_id)}).mappings().first()
    if not order:
        raise HTTPException(status_code=409, detail="El pedido ya no está disponible para despacho")
    lines = db.execute(text("""select product_id,quantity from sales_delivery_items where delivery_id=:id and tenant_id=:tenant"""), {"id": str(delivery_id), "tenant": str(actor.tenant_id)}).mappings().all()
    raw_serials = {str(line.product_id): line.serial_numbers for line in data.lines}
    try:
        products = {product.id: product for product in db.scalars(select(Product).where(Product.tenant_id == actor.tenant_id, Product.id.in_([line["product_id"] for line in lines])).with_for_update()).all()}
        if len(products) != len(lines):
            raise HTTPException(status_code=409, detail="Una línea del despacho ya no tiene un producto activo")
        line_product_ids = {str(line["product_id"]) for line in lines}
        if not set(raw_serials).issubset(line_product_ids):
            raise HTTPException(status_code=422, detail="Se recibieron series para un producto ajeno al despacho")
        for line in lines:
            product_id, quantity = line["product_id"], Decimal(str(line["quantity"]))
            stock = db.scalar(select(Inventory).where(Inventory.tenant_id == actor.tenant_id, Inventory.product_id == product_id, Inventory.warehouse_id == delivery["warehouse_id"]).with_for_update())
            if stock is None:
                raise HTTPException(status_code=422, detail=f"No hay existencias para {products[product_id].name}")
            other_reserved = db.execute(text("""
                select coalesce(sum(quantity),0) from stock_reservations
                where tenant_id=:tenant and product_id=:product and warehouse_id=:warehouse and status='active'
                  and not (reference_type='sales_order' and reference_id=:order)
            """), {"tenant": str(actor.tenant_id), "product": str(product_id), "warehouse": str(delivery["warehouse_id"]), "order": str(order["id"])}).scalar() or Decimal("0")
            if Decimal(str(stock.quantity)) - Decimal(str(other_reserved)) < quantity:
                raise HTTPException(status_code=422, detail=f"No hay disponibilidad suficiente para despachar {products[product_id].name}")
            issue_tracked_stock(db, tenant_id=actor.tenant_id, product=products[product_id], warehouse_id=delivery["warehouse_id"], quantity=quantity,
                                reference=delivery["number"], serial_numbers=raw_serials.get(str(product_id)))
            reservation = db.execute(text("""
                select id, quantity from stock_reservations where tenant_id=:tenant and product_id=:product
                  and warehouse_id=:warehouse and reference_type='sales_order' and reference_id=:order and status='active' for update
            """), {"tenant": str(actor.tenant_id), "product": str(product_id), "warehouse": str(delivery["warehouse_id"]), "order": str(order["id"])}).mappings().first()
            if reservation:
                balance = Decimal(str(reservation["quantity"])) - quantity
                if balance < 0:
                    raise HTTPException(status_code=409, detail="La reserva del pedido no cubre el despacho")
                # quantity has a positive database check. A fully consumed
                # reservation retains its original quantity as audit evidence
                # and becomes non-blocking through status='fulfilled'.
                db.execute(text("""update stock_reservations set quantity=case when :balance=0 then quantity else :balance end,
                    status=case when :balance=0 then 'fulfilled' else 'active' end,
                    released_at=case when :balance=0 then now() else null end, updated_at=now() where id=:id"""), {"balance": balance, "id": str(reservation["id"])})
        db.execute(text("""update sales_deliveries set status='dispatched',dispatched_by=:actor,dispatched_at=now(),updated_at=now()
            where id=:id and tenant_id=:tenant"""), {"actor": str(actor.user_id), "id": str(delivery_id), "tenant": str(actor.tenant_id)})
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="dispatch", module="inventory", entity="sales_delivery", entity_id=delivery_id, detail=delivery["number"])
        result = _delivery_payload(db, tenant_id=actor.tenant_id, delivery_id=delivery_id)
        complete_idempotency(db, claim=idempotency, response_body=result, response_status=status.HTTP_200_OK)
        db.commit()
        return result
    except HTTPException:
        db.rollback()
        raise


@router.post("/{delivery_id}/deliver")
def mark_sales_delivery_delivered(delivery_id: UUID, request: Request, actor: Actor = Depends(require_permission("sales:write")), _: Actor = Depends(require_module("sales_orders")), db: Session = Depends(get_db)):
    idempotency = claim_idempotency(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id,
        operation="sales.deliveries.deliver", key=request.headers.get("Idempotency-Key"), payload={"delivery_id": str(delivery_id)})
    if idempotency and idempotency.is_replay:
        return replay_response(idempotency)
    delivery = db.execute(text("""select * from sales_deliveries where id=:id and tenant_id=:tenant and status='dispatched' for update"""), {"id": str(delivery_id), "tenant": str(actor.tenant_id)}).mappings().first()
    if not delivery:
        raise HTTPException(status_code=409, detail="El despacho debe estar despachado antes de confirmarse como entregado")
    db.execute(text("""update sales_deliveries set status='delivered',delivered_by=:actor,delivered_at=now(),updated_at=now()
        where id=:id and tenant_id=:tenant"""), {"actor": str(actor.user_id), "id": str(delivery_id), "tenant": str(actor.tenant_id)})
    outstanding = db.execute(text("""
        select count(*) from sales_order_items oi
        left join (
            select i.product_id, sum(i.quantity) quantity from sales_delivery_items i
            join sales_deliveries d on d.id=i.delivery_id and d.tenant_id=i.tenant_id
            where d.tenant_id=:tenant and d.sales_order_id=:order and d.status in ('dispatched','delivered') group by i.product_id
        ) shipped on shipped.product_id=oi.product_id
        where oi.tenant_id=:tenant and oi.order_id=:order and coalesce(shipped.quantity,0) < oi.quantity
    """), {"tenant": str(actor.tenant_id), "order": str(delivery["sales_order_id"])}).scalar_one()
    if outstanding == 0:
        db.execute(text("""update sales_orders set status='fulfilled',updated_at=now() where id=:id and tenant_id=:tenant and status='confirmed'"""), {"id": str(delivery["sales_order_id"]), "tenant": str(actor.tenant_id)})
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="deliver", module="sales", entity="sales_delivery", entity_id=delivery_id, detail=delivery["number"])
    result = _delivery_payload(db, tenant_id=actor.tenant_id, delivery_id=delivery_id)
    complete_idempotency(db, claim=idempotency, response_body=result, response_status=status.HTTP_200_OK)
    db.commit()
    return result


@router.post("/{delivery_id}/cancel")
def cancel_sales_delivery(delivery_id: UUID, actor: Actor = Depends(require_permission("sales:write")), _: Actor = Depends(require_module("sales_orders")), db: Session = Depends(get_db)):
    row = db.execute(text("""update sales_deliveries set status='cancelled',updated_at=now()
        where id=:id and tenant_id=:tenant and status='draft' returning *"""), {"id": str(delivery_id), "tenant": str(actor.tenant_id)}).mappings().first()
    if not row:
        raise HTTPException(status_code=409, detail="Solo se puede cancelar un despacho en borrador")
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="cancel", module="sales", entity="sales_delivery", entity_id=delivery_id, detail=row["number"])
    db.commit()
    return _delivery_payload(db, tenant_id=actor.tenant_id, delivery_id=delivery_id)
