from uuid import UUID
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import Customer, Inventory, Product, Supplier, Warehouse
from app.security.auth import Actor, require_permission
from app.schemas.transactions import PurchaseCreate, PurchaseLineCreate, SaleCreate, SaleLineCreate
from app.api.routes.sales import post_sale
from app.api.routes.purchases import receive_purchase
from app.services.approval_documents import create_purchase_order_evidence
from app.services.audit import record

router = APIRouter(tags=["workflow"])

class WorkflowCreate(BaseModel):
    number: str = Field(min_length=1, max_length=40)
    counterparty_id: UUID
    warehouse_id: UUID | None = None
    total: Decimal = Field(default=0, ge=0)

class StatusChange(BaseModel):
    status: str

class WorkflowLine(BaseModel):
    product_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=0)
    unit_value: Decimal = Field(ge=0)

    @field_validator("quantity")
    @classmethod
    def quantity_must_be_whole(cls, value: Decimal) -> Decimal:
        if value != value.to_integral_value():
            raise ValueError("La cantidad debe ser un número entero")
        return value

def _register(prefix: str, table: str, counterparty: str, counterparty_model, write_permission: str, allowed: set[str], module_code: str | None = None):
    @router.get(prefix)
    def list_docs(actor: Actor = Depends(require_permission(write_permission.replace(":write", ":read"), module_code)), db: Session = Depends(get_db)):
        return db.execute(select(__import__("sqlalchemy").text("*")).select_from(__import__("sqlalchemy").text(table)).where(__import__("sqlalchemy").text("tenant_id = :tenant")).params(tenant=str(actor.tenant_id))).mappings().all()

    @router.post(prefix, status_code=status.HTTP_201_CREATED)
    def create_doc(data: WorkflowCreate, actor: Actor = Depends(require_permission(write_permission, module_code)), db: Session = Depends(get_db)):
        if prefix != "/quotes" and not data.warehouse_id:
            raise HTTPException(status_code=422, detail="El almacén es obligatorio")
        counterpart = db.scalar(select(counterparty_model.id).where(counterparty_model.id == data.counterparty_id, counterparty_model.tenant_id == actor.tenant_id, counterparty_model.is_active.is_(True)))
        warehouse = True if not data.warehouse_id else db.scalar(select(Warehouse.id).where(Warehouse.id == data.warehouse_id, Warehouse.tenant_id == actor.tenant_id, Warehouse.is_active.is_(True)))
        if not counterpart or not warehouse:
            raise HTTPException(status_code=404, detail="La contraparte o el almacén no pertenece a la empresa activa")
        values = {"number": str(data.number), "tenant": str(actor.tenant_id), "counterparty": str(data.counterparty_id), "warehouse": str(data.warehouse_id) if data.warehouse_id else None, "total": data.total}
        columns = f"tenant_id, number, {counterparty}, total" + (", warehouse_id" if data.warehouse_id else "")
        params = ":tenant, :number, :counterparty, :total" + (", :warehouse" if data.warehouse_id else "")
        try:
            row = db.execute(__import__("sqlalchemy").text(f"insert into {table} ({columns}) values ({params}) returning *"), values).mappings().one()
            db.commit()
            return row
        except Exception as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="Número duplicado o referencia inválida") from exc

    @router.post(prefix + "/{document_id}/status")
    def change_status(document_id: UUID, data: StatusChange, actor: Actor = Depends(require_permission(write_permission, module_code)), db: Session = Depends(get_db)):
        if table == "sales_orders" and data.status == "confirmed":
            order = db.execute(__import__("sqlalchemy").text("""
                select * from sales_orders where id=:id and tenant_id=:tenant and status='draft' for update
            """), {"id": str(document_id), "tenant": str(actor.tenant_id)}).mappings().first()
            if not order:
                raise HTTPException(status_code=409, detail="El pedido no existe, ya fue confirmado o no puede modificarse")
            lines = db.execute(__import__("sqlalchemy").text("""
                select product_id, quantity from sales_order_items where order_id=:id and tenant_id=:tenant order by product_id
            """), {"id": str(document_id), "tenant": str(actor.tenant_id)}).mappings().all()
            if not lines:
                raise HTTPException(status_code=422, detail="Agrega al menos una línea antes de confirmar el pedido")
            # Lock balances in a stable product order before reading reservations.
            # It prevents two confirmations from consuming the same available stock.
            for line in lines:
                stock = db.scalar(select(Inventory).where(
                    Inventory.tenant_id == actor.tenant_id,
                    Inventory.product_id == line["product_id"],
                    Inventory.warehouse_id == order["warehouse_id"],
                ).with_for_update())
                if stock is None:
                    raise HTTPException(status_code=422, detail="No existe saldo disponible para uno de los productos del pedido")
                reserved = db.execute(__import__("sqlalchemy").text("""
                    select coalesce(sum(quantity),0) from stock_reservations
                    where tenant_id=:tenant and product_id=:product and warehouse_id=:warehouse and status='active'
                """), {"tenant": str(actor.tenant_id), "product": str(line["product_id"]), "warehouse": str(order["warehouse_id"])}).scalar() or Decimal("0")
                if Decimal(str(stock.quantity)) - Decimal(str(reserved)) < Decimal(str(line["quantity"])):
                    raise HTTPException(status_code=422, detail="No hay disponibilidad suficiente para confirmar el pedido")
            for line in lines:
                db.execute(__import__("sqlalchemy").text("""
                    insert into stock_reservations(tenant_id,product_id,warehouse_id,reference_type,reference_id,reference,quantity,note,created_by)
                    values(:tenant,:product,:warehouse,'sales_order',:order,:reference,:quantity,:note,:actor)
                """), {"tenant": str(actor.tenant_id), "product": str(line["product_id"]), "warehouse": str(order["warehouse_id"]),
                           "order": str(document_id), "reference": order["number"], "quantity": line["quantity"],
                           "note": f"Reserva automática del pedido {order['number']}", "actor": str(actor.user_id)})
            row = db.execute(__import__("sqlalchemy").text("""
                update sales_orders set status='confirmed',updated_at=now() where id=:id and tenant_id=:tenant returning *
            """), {"id": str(document_id), "tenant": str(actor.tenant_id)}).mappings().one()
            record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="confirm_and_reserve", module="sales", entity="sales_order", entity_id=document_id, detail=order["number"])
            db.commit()
            return row
        if table == "sales_orders" and data.status == "cancelled":
            row = db.execute(__import__("sqlalchemy").text("""
                update sales_orders set status='cancelled',updated_at=now()
                where id=:id and tenant_id=:tenant and status in ('draft','confirmed') returning *
            """), {"id": str(document_id), "tenant": str(actor.tenant_id)}).mappings().first()
            if not row:
                raise HTTPException(status_code=409, detail="El pedido no puede cancelarse en su estado actual")
            db.execute(__import__("sqlalchemy").text("""
                update stock_reservations set status='released',released_at=now(),updated_at=now()
                where tenant_id=:tenant and reference_type='sales_order' and reference_id=:order and status='active'
            """), {"tenant": str(actor.tenant_id), "order": str(document_id)})
            record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="cancel_and_release", module="sales", entity="sales_order", entity_id=document_id, detail=row["number"])
            db.commit()
            return row
        if table == "purchase_orders" and data.status == "submitted":
            order = db.execute(__import__("sqlalchemy").text("""
                select * from purchase_orders where id=:id and tenant_id=:tenant and status='draft' for update
            """), {"id": str(document_id), "tenant": str(actor.tenant_id)}).mappings().first()
            if not order:
                raise HTTPException(status_code=409, detail="La orden no existe o ya fue enviada")
            line_count = db.execute(__import__("sqlalchemy").text("""
                select count(*) from purchase_order_items where purchase_order_id=:id and tenant_id=:tenant
            """), {"id": str(document_id), "tenant": str(actor.tenant_id)}).scalar_one()
            if not line_count:
                raise HTTPException(status_code=422, detail="Agrega al menos una línea antes de enviar la orden")
            policy = db.execute(__import__("sqlalchemy").text("""
                select id from approval_policies
                where tenant_id=:tenant and module='purchases' and action='purchase_order'
                  and is_active=true and min_amount <= :amount
                order by min_amount desc limit 1
            """), {"tenant": str(actor.tenant_id), "amount": order["total"]}).scalar()
            request_id = db.execute(__import__("sqlalchemy").text("""
                insert into approval_requests(tenant_id, policy_id, module, action, entity_type, entity_id, amount, payload, requested_by)
                values(:tenant, :policy, 'purchases', 'purchase_order', 'purchase_order', :order, :amount,
                       jsonb_build_object('number', :number, 'total', :amount), :actor)
                returning id
            """), {"tenant": str(actor.tenant_id), "policy": str(policy) if policy else None,
                   "order": str(document_id), "amount": order["total"], "number": order["number"], "actor": str(actor.user_id)}).scalar_one()
            create_purchase_order_evidence(
                db, tenant_id=actor.tenant_id, order_id=document_id, approval_request_id=request_id, requested_by=actor.user_id,
            )
            row = db.execute(__import__("sqlalchemy").text("""
                update purchase_orders set status='submitted' where id=:id and tenant_id=:tenant returning *
            """), {"id": str(document_id), "tenant": str(actor.tenant_id)}).mappings().one()
            record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="submit", module="purchases", entity="purchase_order", entity_id=document_id)
            db.commit()
            return row
        if table == "purchase_orders" and data.status == "approved":
            raise HTTPException(status_code=403, detail="La orden debe aprobarse desde el flujo de aprobaciones")
        if table == "purchase_orders" and data.status != "cancelled":
            raise HTTPException(status_code=422, detail="Transición de orden de compra no permitida")
        if data.status not in allowed:
            raise HTTPException(status_code=422, detail="Estado no permitido")
        row = db.execute(__import__("sqlalchemy").text(f"update {table} set status = :status where id = :id and tenant_id = :tenant returning *"), {"status": data.status, "id": str(document_id), "tenant": str(actor.tenant_id)}).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Documento no encontrado")
        db.commit()
        return row

_register("/quotes", "sales_quotes", "customer_id", Customer, "sales:write", {"draft", "approved", "cancelled"}, "quotes")
_register("/sales-orders", "sales_orders", "customer_id", Customer, "sales:write", {"draft", "confirmed", "cancelled"}, "sales_orders")
_register("/purchase-orders", "purchase_orders", "supplier_id", Supplier, "purchases:write", {"draft", "submitted", "cancelled"})


@router.get("/sales-orders/{order_id}/lines")
def list_sales_order_lines(order_id: UUID, actor: Actor = Depends(require_permission("sales:read", "sales_orders")), db: Session = Depends(get_db)):
    """Read the immutable commercial lines used to prepare a delivery."""
    order = db.execute(__import__("sqlalchemy").text("""
        select id from sales_orders where id=:id and tenant_id=:tenant
    """), {"id": str(order_id), "tenant": str(actor.tenant_id)}).first()
    if not order:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")
    return db.execute(__import__("sqlalchemy").text("""
        select i.product_id, p.sku, p.name as product_name, p.tracking_mode, i.quantity, i.unit_price, i.line_total
        from sales_order_items i
        join products p on p.id=i.product_id and p.tenant_id=i.tenant_id
        where i.order_id=:id and i.tenant_id=:tenant
        order by p.name, i.id
    """), {"id": str(order_id), "tenant": str(actor.tenant_id)}).mappings().all()

def _line_endpoint(prefix: str, header: str, detail: str, key: str, value_column: str, permission: str, module_code: str | None = None):
    @router.post(prefix + "/{document_id}/lines", status_code=status.HTTP_201_CREATED)
    def add_line(document_id: UUID, data: WorkflowLine, actor: Actor = Depends(require_permission(permission, module_code)), db: Session = Depends(get_db)):
        header_row = db.execute(__import__("sqlalchemy").text(f"select status from {header} where id=:id and tenant_id=:tenant"), {"id": str(document_id), "tenant": str(actor.tenant_id)}).mappings().first()
        if not header_row or header_row["status"] != "draft":
            raise HTTPException(status_code=409, detail="Documento inexistente o no editable")
        product = db.scalar(select(Product.id).where(Product.id == data.product_id, Product.tenant_id == actor.tenant_id, Product.is_active.is_(True)))
        if not product:
            raise HTTPException(status_code=404, detail="Producto no encontrado en la empresa activa")
        total = data.quantity * data.unit_value
        try:
            db.execute(__import__("sqlalchemy").text(f"insert into {detail}(tenant_id,{key},product_id,quantity,{value_column},line_total) values(:tenant,:id,:product,:quantity,:value,:total)"), {"tenant":str(actor.tenant_id),"id":str(document_id),"product":str(data.product_id),"quantity":data.quantity,"value":data.unit_value,"total":total})
            db.execute(__import__("sqlalchemy").text(f"update {header} set total=(select coalesce(sum(line_total),0) from {detail} where {key}=:id and tenant_id=:tenant) where id=:id and tenant_id=:tenant"), {"id":str(document_id),"tenant":str(actor.tenant_id)})
            db.commit()
        except Exception as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="Producto inválido o ya incluido") from exc
        return {"document_id": document_id, "line_total": total}

_line_endpoint("/quotes", "sales_quotes", "sales_quote_items", "quote_id", "unit_price", "sales:write", "quotes")
_line_endpoint("/sales-orders", "sales_orders", "sales_order_items", "order_id", "unit_price", "sales:write", "sales_orders")
_line_endpoint("/purchase-orders", "purchase_orders", "purchase_order_items", "purchase_order_id", "unit_cost", "purchases:write")

@router.post("/quotes/{quote_id}/to-sales-order", status_code=status.HTTP_201_CREATED)
def quote_to_order(quote_id: UUID, number: str, warehouse_id: UUID, actor: Actor = Depends(require_permission("sales:write", "quotes")), db: Session = Depends(get_db)):
    quote = db.execute(__import__("sqlalchemy").text("select * from sales_quotes where id=:id and tenant_id=:tenant and status='approved' and sales_order_id is null for update"), {"id":str(quote_id),"tenant":str(actor.tenant_id)}).mappings().first()
    if not quote:
        raise HTTPException(status_code=409, detail="Cotización no aprobada o ya convertida")
    warehouse = db.scalar(select(Warehouse.id).where(Warehouse.id == warehouse_id, Warehouse.tenant_id == actor.tenant_id, Warehouse.is_active.is_(True)))
    if not warehouse:
        raise HTTPException(status_code=404, detail="Almacén no encontrado en la empresa activa")
    try:
        order = db.execute(__import__("sqlalchemy").text("insert into sales_orders(tenant_id,number,customer_id,warehouse_id,quote_id,total) values(:tenant,:number,:customer,:warehouse,:quote,:total) returning id"), {"tenant":str(actor.tenant_id),"number":number,"customer":quote["customer_id"],"warehouse":str(warehouse_id),"quote":str(quote_id),"total":quote["total"]}).scalar_one()
        db.execute(__import__("sqlalchemy").text("insert into sales_order_items(tenant_id,order_id,product_id,quantity,unit_price,line_total) select tenant_id,:order,product_id,quantity,unit_price,line_total from sales_quote_items where quote_id=:quote and tenant_id=:tenant"), {"order":str(order),"quote":str(quote_id),"tenant":str(actor.tenant_id)})
        db.execute(__import__("sqlalchemy").text("""
            update sales_quotes set sales_order_id=:order
            where id=:quote and tenant_id=:tenant and sales_order_id is null
        """), {"order":str(order),"quote":str(quote_id),"tenant":str(actor.tenant_id)})
        db.commit()
        return {"sales_order_id": order}
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="No se pudo convertir la cotización") from exc

@router.post("/sales-orders/{order_id}/to-sale", status_code=status.HTTP_201_CREATED)
def order_to_sale(order_id: UUID, number: str, request: Request, actor: Actor = Depends(require_permission("sales:write", "sales_orders")), db: Session = Depends(get_db)):
    order = db.execute(__import__("sqlalchemy").text("select * from sales_orders where id=:id and tenant_id=:tenant and status in ('confirmed','fulfilled') for update"), {"id":str(order_id),"tenant":str(actor.tenant_id)}).mappings().first()
    if not order:
        raise HTTPException(status_code=409, detail="Pedido no confirmado o inexistente")
    exists = db.execute(__import__("sqlalchemy").text("""
        select 1 from sales where sales_order_id=:id and tenant_id=:tenant
    """), {"id":str(order_id), "tenant":str(actor.tenant_id)}).first()
    if exists:
        raise HTTPException(status_code=409, detail="El pedido ya fue convertido")
    rows = db.execute(__import__("sqlalchemy").text("select product_id,quantity,unit_price from sales_order_items where order_id=:id and tenant_id=:tenant"), {"id":str(order_id),"tenant":str(actor.tenant_id)}).mappings().all()
    if not rows:
        raise HTTPException(status_code=422, detail="El pedido no tiene líneas")
    delivery_count = db.execute(__import__("sqlalchemy").text("""
        select count(*) from sales_deliveries where tenant_id=:tenant and sales_order_id=:order and status in ('dispatched','delivered')
    """), {"tenant": str(actor.tenant_id), "order": str(order_id)}).scalar_one()
    if delivery_count:
        outstanding = db.execute(__import__("sqlalchemy").text("""
            select count(*) from sales_order_items oi
            left join (
                select i.product_id, sum(i.quantity) quantity from sales_delivery_items i
                join sales_deliveries d on d.id=i.delivery_id and d.tenant_id=i.tenant_id
                where d.tenant_id=:tenant and d.sales_order_id=:order and d.status in ('dispatched','delivered') group by i.product_id
            ) shipped on shipped.product_id=oi.product_id
            where oi.tenant_id=:tenant and oi.order_id=:order and coalesce(shipped.quantity,0) < oi.quantity
        """), {"tenant": str(actor.tenant_id), "order": str(order_id)}).scalar_one()
        if outstanding:
            raise HTTPException(status_code=409, detail="El pedido tiene despachos parciales pendientes antes de facturarse")
        # Only a trusted in-process handoff may suppress the issue: public
        # sales requests always retain their normal inventory behaviour.
        request.state.stock_already_issued_by_delivery = True
    request.state.sales_order_reservation_id = order_id
    sale = post_sale(request, SaleCreate(number=number, customer_id=order["customer_id"], warehouse_id=order["warehouse_id"], lines=[SaleLineCreate(**dict(r)) for r in rows]), actor, db)
    db.execute(__import__("sqlalchemy").text("""
        update sales set sales_order_id=:order
        where id=:sale and tenant_id=:tenant and sales_order_id is null
    """), {"order":str(order_id),"sale":str(sale.id),"tenant":str(actor.tenant_id)})
    db.execute(__import__("sqlalchemy").text("""
        update sales_orders set status='confirmed'
        where id=:id and tenant_id=:tenant and status='confirmed'
    """), {"id":str(order_id), "tenant":str(actor.tenant_id)})
    if not delivery_count:
        db.execute(__import__("sqlalchemy").text("""
            update stock_reservations set status='fulfilled',released_at=now(),updated_at=now()
            where tenant_id=:tenant and reference_type='sales_order' and reference_id=:order and status='active'
        """), {"tenant": str(actor.tenant_id), "order": str(order_id)})
    db.commit()
    return sale

@router.post("/purchase-orders/{order_id}/to-purchase", status_code=status.HTTP_201_CREATED)
def order_to_purchase(order_id: UUID, number: str, actor: Actor = Depends(require_permission("purchases:write")), db: Session = Depends(get_db)):
    order = db.execute(__import__("sqlalchemy").text("select * from purchase_orders where id=:id and tenant_id=:tenant and status='approved' for update"), {"id":str(order_id),"tenant":str(actor.tenant_id)}).mappings().first()
    if not order:
        raise HTTPException(status_code=409, detail="Orden no aprobada o inexistente")
    approval = db.execute(__import__("sqlalchemy").text("""
        select r.status, r.requested_by, r.decided_by,
               coalesce(p.require_executor_separation, true) as require_executor_separation
        from approval_requests r
        left join approval_policies p on p.id=r.policy_id and p.tenant_id=r.tenant_id
        where r.tenant_id=:tenant and r.entity_type='purchase_order' and r.entity_id=:id
        order by r.created_at desc limit 1
    """), {"id": str(order_id), "tenant": str(actor.tenant_id)}).mappings().first()
    if not approval or approval["status"] != "approved":
        raise HTTPException(status_code=409, detail="La orden requiere una aprobación vigente antes de recibirse")
    if approval["require_executor_separation"] and str(actor.user_id) in {
        str(approval["requested_by"]), str(approval["decided_by"]),
    }:
        raise HTTPException(status_code=403, detail="La segregación de funciones exige que otra persona reciba la orden de compra")
    if db.execute(__import__("sqlalchemy").text("""
        select 1 from purchases where purchase_order_id=:id and tenant_id=:tenant
    """), {"id":str(order_id), "tenant":str(actor.tenant_id)}).first():
        raise HTTPException(status_code=409, detail="La orden ya fue convertida")
    rows = db.execute(__import__("sqlalchemy").text("select product_id,quantity,unit_cost from purchase_order_items where purchase_order_id=:id and tenant_id=:tenant"), {"id":str(order_id),"tenant":str(actor.tenant_id)}).mappings().all()
    if not rows:
        raise HTTPException(status_code=422, detail="La orden no tiene líneas")
    purchase = receive_purchase(PurchaseCreate(number=number, supplier_id=order["supplier_id"], warehouse_id=order["warehouse_id"], lines=[PurchaseLineCreate(**dict(r)) for r in rows]), actor, db)
    db.execute(__import__("sqlalchemy").text("""
        update purchases set purchase_order_id=:order
        where id=:purchase and tenant_id=:tenant and purchase_order_id is null
    """), {"order":str(order_id),"purchase":str(purchase.id),"tenant":str(actor.tenant_id)})
    db.commit()
    return purchase
