from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.core.config import get_settings
from app.models.entities import AccountReceivable, Branch, Customer, ElectronicDocument, LegalEntity, Product, Sale, SaleItem, Warehouse
from app.schemas.electronic_billing import ElectronicDocumentCreate, ElectronicDocumentOut, VoidElectronicDocument
from app.schemas.transactions import CancelTransaction, SaleCreate, SaleOut
from app.schemas.pagination import paginate
from app.security.auth import Actor, require_permission
from app.services.audit import record
from app.services.apisunat import APISunatError, build_document_body, get_document, next_correlative, void_bill
from app.services.traceability import issue_tracked_stock, return_tracked_sale_stock
from app.services.decolecta import DecolectaError, lookup_tax_id
from app.services.electronic_delivery import provider_status
from app.services.accounting import post_entry, reverse_entry, split_tax
from app.services.idempotency import claim as claim_idempotency, complete as complete_idempotency, replay_response
from app.services.outbox import enqueue
from app.services.operational_context import resolve_warehouse_context
from app.services.document_sequences import allocate_document_number

router = APIRouter(prefix="/sales", tags=["sales"])


@router.get("/warehouses")
def list_sale_warehouses(actor: Actor = Depends(require_permission("sales:read")), db: Session = Depends(get_db)):
    """Expose only eligible warehouse identities needed when recording a sale."""
    return db.scalars(select(Warehouse).where(
        Warehouse.tenant_id == actor.tenant_id,
        Warehouse.is_active.is_(True),
    ).order_by(Warehouse.name)).all()


@router.get("")
def list_sales(
    actor: Actor = Depends(require_permission("sales:read")),
    db: Session = Depends(get_db),
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=100),
):
    statement = select(Sale).where(Sale.tenant_id == actor.tenant_id).order_by(Sale.created_at.desc(), Sale.id)
    if not isinstance(page, int) and not isinstance(page_size, int):
        return db.scalars(statement).all()
    return paginate(db, statement, key="sales", page=page or 1, page_size=page_size or 25)


@router.get("/customer-lookup/{tax_id}")
def lookup_sale_customer(tax_id: str, actor: Actor = Depends(require_permission("sales:write")), db: Session = Depends(get_db)):
    """Return an existing ERP customer first, otherwise authoritative DNI/RUC data."""
    normalized = tax_id.strip()
    existing = db.scalar(select(Customer).where(Customer.tenant_id == actor.tenant_id, Customer.tax_id == normalized, Customer.is_active.is_(True)))
    if existing:
        return {"existing_customer_id": str(existing.id), "tax_id": normalized, "name": existing.name, "address": "", "document_type": "DNI" if len(normalized) == 8 else "RUC"}
    try:
        return {"existing_customer_id": None, **lookup_tax_id(get_settings(), normalized)}
    except DecolectaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _electronic_document(sale_id: str, actor: Actor, db: Session) -> tuple[Sale, ElectronicDocument | None]:
    sale = db.scalar(select(Sale).where(Sale.id == sale_id, Sale.tenant_id == actor.tenant_id))
    if not sale:
        raise HTTPException(status_code=404, detail="La venta no existe")
    document = db.scalar(select(ElectronicDocument).where(ElectronicDocument.sale_id == sale.id, ElectronicDocument.tenant_id == actor.tenant_id))
    return sale, document


@router.get("/{sale_id}/electronic-document", response_model=ElectronicDocumentOut | None)
def get_electronic_document(sale_id: str, actor: Actor = Depends(require_permission("electronic_billing:read")), db: Session = Depends(get_db)):
    _, document = _electronic_document(sale_id, actor, db)
    return document


@router.post("/{sale_id}/electronic-document", response_model=ElectronicDocumentOut, status_code=status.HTTP_201_CREATED)
def issue_electronic_document(sale_id: str, request: Request, response: Response, data: ElectronicDocumentCreate, actor: Actor = Depends(require_permission("electronic_billing:write")), db: Session = Depends(get_db)):
    idempotency = claim_idempotency(
        db,
        tenant_id=actor.tenant_id,
        actor_user_id=actor.user_id,
        operation=f"electronic_billing.issue.{sale_id}",
        key=request.headers.get("Idempotency-Key"),
        payload={"sale_id": sale_id, **data.model_dump(mode="json")},
    )
    if idempotency and idempotency.is_replay:
        return replay_response(idempotency)
    sale, existing = _electronic_document(sale_id, actor, db)
    if sale.status != "posted":
        raise HTTPException(status_code=409, detail="Solo se pueden emitir comprobantes de ventas procesadas")
    if existing:
        raise HTTPException(status_code=409, detail="Esta venta ya tiene un comprobante electrónico")
    if not sale.legal_entity_id or not sale.branch_id:
        raise HTTPException(
            status_code=422,
            detail="La venta no tiene entidad legal y sucursal. Configura la sucursal del almacén y registra una nueva venta antes de facturar",
        )
    legal_entity = db.scalar(select(LegalEntity).where(
        LegalEntity.id == sale.legal_entity_id,
        LegalEntity.tenant_id == actor.tenant_id,
        LegalEntity.is_active.is_(True),
    ))
    branch = db.scalar(select(Branch).where(
        Branch.id == sale.branch_id,
        Branch.tenant_id == actor.tenant_id,
        Branch.legal_entity_id == sale.legal_entity_id,
        Branch.is_active.is_(True),
    ))
    if legal_entity is None or branch is None:
        raise HTTPException(
            status_code=422,
            detail="La entidad legal y sucursal son obligatorias para emitir un comprobante electrónico",
        )
    # A single APISUNAT persona is configured for this installation. Do not
    # create a document that claims a different issuer than that credential.
    settings = get_settings()
    if legal_entity.tax_id != (settings.apisunat_issuer_ruc or "").strip():
        raise HTTPException(
            status_code=422,
            detail="La credencial APISUNAT configurada corresponde a otra entidad legal; configura una persona compatible antes de emitir",
        )
    customer = db.scalar(select(Customer).where(Customer.id == sale.customer_id, Customer.tenant_id == actor.tenant_id))
    items = db.execute(select(SaleItem, Product).join(Product, Product.id == SaleItem.product_id).where(SaleItem.sale_id == sale.id, SaleItem.tenant_id == actor.tenant_id)).all()
    if not customer or not items:
        raise HTTPException(status_code=409, detail="La venta no tiene cliente o líneas válidas para facturar")
    tax_id = (customer.tax_id or "").strip()
    if data.document_type == "01" and (len(tax_id) != 11 or not tax_id.isdigit()):
        raise HTTPException(status_code=422, detail="Para emitir Factura el cliente debe tener un RUC de 11 dígitos")
    if data.document_type == "03" and (len(tax_id) not in {8, 11} or not tax_id.isdigit()):
        raise HTTPException(status_code=422, detail="Para emitir Boleta el cliente debe tener DNI de 8 dígitos o RUC de 11 dígitos")
    series = settings.apisunat_invoice_series if data.document_type == "01" else settings.apisunat_receipt_series
    try:
        correlative = next_correlative(settings, data.document_type, series)
    except APISunatError as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    taxable = (sale.total / Decimal("1.18")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    now = datetime.now(timezone.utc)
    issue_date, issue_time = now.date().isoformat(), now.strftime("%H:%M:%S")
    file_name = f"{settings.apisunat_issuer_ruc}-{data.document_type}-{series}-{correlative}"
    body = build_document_body(
        sale_number=sale.number, issue_date=issue_date, issue_time=issue_time,
        document_type=data.document_type, series=series, correlative=correlative,
        issuer={
            "ruc": legal_entity.tax_id,
            "trade_name": legal_entity.trade_name or legal_entity.legal_name,
            "legal_name": legal_entity.legal_name,
            "address": branch.address or legal_entity.fiscal_address,
        },
        customer={"tax_id": tax_id, "name": customer.name, "address": ""},
        lines=[{"quantity": item.quantity, "unit_price": item.unit_price, "unit": product.unit, "description": product.name} for item, product in items],
        total=sale.total,
    )
    # Reserve the document and its provider payload in the same transaction as
    # the outbox event. The web request never crosses the provider boundary.
    # This removes the ambiguous "provider accepted but HTTP timed out" case.
    document = ElectronicDocument(
        tenant_id=actor.tenant_id, sale_id=sale.id,
        legal_entity_id=sale.legal_entity_id, branch_id=sale.branch_id, document_type=data.document_type,
        series=series, correlative=correlative, file_name=file_name, status="reserved",
        taxable_amount=taxable, tax_amount=sale.total - taxable, total_amount=sale.total,
        provider_payload={
            "submission": {
                "document_body": body,
                "customer_email": customer.email or None,
            },
            "issuer_snapshot": {
                "legal_entity_id": str(legal_entity.id),
                "branch_id": str(branch.id),
                "tax_id": legal_entity.tax_id,
                "legal_name": legal_entity.legal_name,
                "branch_name": branch.name,
            },
        },
    )
    db.add(document)
    try:
        db.flush()
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="reserve", module="electronic_billing", entity="electronic_document", entity_id=document.id, detail=file_name)
        enqueue(
            db,
            tenant_id=actor.tenant_id,
            topic="cpe.submit",
            payload={
                "tenant_id": str(actor.tenant_id),
                "document_id": str(document.id),
                "actor_user_id": str(actor.user_id),
            },
        )
        response_body = ElectronicDocumentOut.model_validate(document).model_dump(mode="json")
        complete_idempotency(
            db,
            claim=idempotency,
            response_body=response_body,
            response_status=status.HTTP_202_ACCEPTED,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El correlativo ya fue reservado; vuelve a intentarlo sin reemitir") from exc
    db.refresh(document)
    response.status_code = status.HTTP_202_ACCEPTED
    return document


@router.post("/{sale_id}/electronic-document/refresh", response_model=ElectronicDocumentOut)
def refresh_electronic_document(sale_id: str, actor: Actor = Depends(require_permission("electronic_billing:read")), db: Session = Depends(get_db)):
    _, document = _electronic_document(sale_id, actor, db)
    if not document or not document.provider_document_id:
        raise HTTPException(status_code=404, detail="La venta no tiene un comprobante electrónico emitido")
    try:
        response = get_document(get_settings(), document.provider_document_id)
    except APISunatError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    document.status = provider_status(response.get("status"))
    document.xml_url = response.get("xml") if isinstance(response.get("xml"), str) else document.xml_url
    document.cdr_url = response.get("cdr") if isinstance(response.get("cdr"), str) else document.cdr_url
    document.provider_payload = response
    document.last_error = None if document.status in {"pending", "accepted"} else str(response.get("faults") or response.get("notes") or "APISUNAT reportó observaciones")
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="refresh", module="electronic_billing", entity="electronic_document", entity_id=document.id)
    db.commit()
    db.refresh(document)
    return document


@router.post("/{sale_id}/electronic-document/void", response_model=ElectronicDocumentOut)
def void_electronic_document(sale_id: str, data: VoidElectronicDocument, actor: Actor = Depends(require_permission("electronic_billing:write")), db: Session = Depends(get_db)):
    _, document = _electronic_document(sale_id, actor, db)
    if not document or not document.provider_document_id:
        raise HTTPException(status_code=404, detail="La venta no tiene un comprobante electrónico emitido")
    if document.status in {"void_pending", "voided"}:
        raise HTTPException(status_code=409, detail="El comprobante ya está en proceso de anulación o anulado")
    try:
        response = void_bill(get_settings(), document.provider_document_id, data.reason)
    except APISunatError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    document.status = "void_pending"
    document.provider_payload = response
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="void", module="electronic_billing", entity="electronic_document", entity_id=document.id, detail=data.reason)
    db.commit()
    db.refresh(document)
    return document


@router.post("", response_model=SaleOut, status_code=status.HTTP_201_CREATED)
def post_sale(request: Request, data: SaleCreate, actor: Actor = Depends(require_permission("sales:write")), db: Session = Depends(get_db)):
    idempotency = claim_idempotency(
        db,
        tenant_id=actor.tenant_id,
        actor_user_id=actor.user_id,
        operation="sales.create",
        key=request.headers.get("Idempotency-Key"),
        payload=data.model_dump(mode="json"),
    )
    if idempotency and idempotency.is_replay:
        return replay_response(idempotency)
    customer_id = data.customer_id
    if data.customer:
        existing = db.scalar(select(Customer).where(Customer.tenant_id == actor.tenant_id, Customer.tax_id == data.customer.tax_id).with_for_update())
        if existing:
            if not existing.is_active:
                raise HTTPException(status_code=409, detail="El cliente asociado a este documento está inactivo")
            customer_id = existing.id
        else:
            customer = Customer(
                tenant_id=actor.tenant_id,
                code=f"CLI-{data.customer.tax_id}",
                name=data.customer.name.strip(),
                tax_id=data.customer.tax_id,
                email=data.customer.email or None,
                phone=data.customer.phone or None,
            )
            db.add(customer)
            db.flush()
            customer_id = customer.id
            record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="create_from_sale", module="customers", entity="customer", entity_id=customer.id, detail=f"Cliente creado durante venta: {data.customer.tax_id}")
    customer = db.scalar(select(Customer.id).where(Customer.id == customer_id, Customer.tenant_id == actor.tenant_id, Customer.is_active.is_(True)))
    operational_context = resolve_warehouse_context(
        db, tenant_id=actor.tenant_id, warehouse_id=data.warehouse_id,
    )
    product_ids = {line.product_id for line in data.lines}
    products = set(db.scalars(select(Product.id).where(Product.tenant_id == actor.tenant_id, Product.id.in_(product_ids), Product.is_active.is_(True))).all())
    if not customer or products != product_ids:
        raise HTTPException(status_code=404, detail="Cliente, almacén o producto no pertenece a la empresa activa")
    total = sum((line.quantity * line.unit_price for line in data.lines), Decimal("0"))
    number = allocate_document_number(
        db, tenant_id=actor.tenant_id, module="sales", document_type="sale",
        legal_entity_id=operational_context.legal_entity.id if operational_context.legal_entity else None,
        branch_id=operational_context.branch.id if operational_context.branch else None,
    )
    sale = Sale(
        tenant_id=actor.tenant_id, number=number, customer_id=customer_id,
        warehouse_id=data.warehouse_id,
        legal_entity_id=operational_context.legal_entity.id if operational_context.legal_entity else None,
        branch_id=operational_context.branch.id if operational_context.branch else None,
        status="posted", total=total, note=data.note,
    )
    db.add(sale)
    try:
        db.flush()
        db.add(AccountReceivable(tenant_id=actor.tenant_id, sale_id=sale.id, customer_id=customer_id, original_amount=total, balance_amount=total, status="open", due_date=data.due_date or datetime.now(timezone.utc).date()))
        products_by_id = {product.id: product for product in db.scalars(select(Product).where(
            Product.tenant_id == actor.tenant_id,
            Product.id.in_(product_ids),
        ).with_for_update()).all()}
        for line in data.lines:
            db.add(SaleItem(tenant_id=actor.tenant_id, sale_id=sale.id, product_id=line.product_id, quantity=line.quantity, unit_price=line.unit_price, line_total=line.quantity * line.unit_price))
            issue_tracked_stock(
                db, tenant_id=actor.tenant_id, product=products_by_id[line.product_id],
                warehouse_id=data.warehouse_id, quantity=line.quantity, reference=number,
                serial_numbers=line.serial_numbers,
            )
        net, tax = split_tax(total)
        post_entry(db, tenant_id=actor.tenant_id, actor_id=actor.user_id, number=f"AS-{number}", description=f"Venta {number}", source_type="sale", source_id=sale.id, lines=[("1210", total, Decimal("0")), ("7010", Decimal("0"), net), ("4010", Decimal("0"), tax)])
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="post", module="sales", entity="sale", entity_id=sale.id)
        complete_idempotency(
            db,
            claim=idempotency,
            response_body=SaleOut.model_validate(sale).model_dump(mode="json"),
            response_status=status.HTTP_201_CREATED,
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El número de venta ya existe en esta empresa") from exc
    db.refresh(sale)
    return sale


@router.post("/{sale_id}/cancel", response_model=SaleOut)
def cancel_sale(sale_id: str, data: CancelTransaction, actor: Actor = Depends(require_permission("sales:write")), db: Session = Depends(get_db)):
    sale = db.scalar(select(Sale).where(Sale.id == sale_id, Sale.tenant_id == actor.tenant_id).with_for_update())
    account = db.scalar(select(AccountReceivable).where(AccountReceivable.sale_id == sale_id, AccountReceivable.tenant_id == actor.tenant_id).with_for_update())
    if not sale or sale.status != "posted" or not account:
        raise HTTPException(status_code=409, detail="La venta no se puede cancelar")
    if account.balance_amount != account.original_amount:
        raise HTTPException(status_code=409, detail="No se puede cancelar una venta con cobros aplicados")
    electronic_document = db.scalar(select(ElectronicDocument).where(
        ElectronicDocument.sale_id == sale.id,
        ElectronicDocument.tenant_id == actor.tenant_id,
    ))
    if electronic_document and electronic_document.status not in {"voided", "rejected", "exception"}:
        raise HTTPException(status_code=409, detail="Primero anula el comprobante electrónico antes de cancelar la venta")
    items = db.scalars(select(SaleItem).where(SaleItem.sale_id == sale.id, SaleItem.tenant_id == actor.tenant_id)).all()
    for item in items:
        product = db.scalar(select(Product).where(
            Product.id == item.product_id,
            Product.tenant_id == actor.tenant_id,
        ).with_for_update())
        if not product:
            raise HTTPException(status_code=409, detail="No se encontró el producto de una línea de venta")
        return_tracked_sale_stock(
            db, tenant_id=actor.tenant_id, product=product, warehouse_id=sale.warehouse_id,
            quantity=item.quantity, sale_reference=sale.number, reversal_reference=f"CANCEL-{sale.number}",
        )
    sale.status = "cancelled"
    account.status = "cancelled"
    reverse_entry(db, tenant_id=actor.tenant_id, actor_id=actor.user_id, number=f"REV-{sale.number}", description=f"Reversión de venta {sale.number}", original_source_type="sale", original_source_id=sale.id, reversal_source_type="sale_cancel")
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="cancel", module="sales", entity="sale", entity_id=sale.id)
    db.commit()
    db.refresh(sale)
    return sale
