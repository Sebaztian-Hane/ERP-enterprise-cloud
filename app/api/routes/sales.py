from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.core.config import get_settings
from app.models.entities import AccountReceivable, Branch, Customer, ElectronicDocument, ElectronicDocumentArtifact, ElectronicDocumentEvent, Inventory, LegalEntity, Payment, PaymentAllocation, PosPaymentMethodConfig, PosReceipt, Product, Sale, SaleItem, Warehouse
from app.schemas.electronic_billing import ElectronicArtifactDownloadOut, ElectronicDocumentArtifactOut, ElectronicDocumentCreate, ElectronicDocumentEventOut, ElectronicDocumentOut, ElectronicDocumentTraceOut, VoidElectronicDocument
from app.schemas.transactions import CancelTransaction, CommercialReturnCreate, CommercialReturnOut, PosCheckoutCreate, PosTenderCreate, PriceListQuoteCreate, SaleCreate, SaleOut
from app.schemas.pagination import paginate
from app.security.auth import Actor, require_permission, require_second_factor
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
from app.services.payment_terms import resolve_payment_term
from app.services.taxes import resolve_default_tax_rate
from app.services.cost_centers import resolve_cost_center
from app.services.apisunat_credentials import resolve_apisunat_credentials
from app.services.cpe_evidence import capture_provider_evidence, record_cpe_event
from app.services.pos_fiscal import cpe_customer_payload, validate_administrative_sale_customer, validate_pos_fiscal_selection
from app.storage.supabase import signed_url

router = APIRouter(prefix="/sales", tags=["sales"])


def _validate_pos_tender_total(*, total: Decimal, tenders: list[PosTenderCreate]) -> Decimal:
    tender_total = sum((tender.amount for tender in tenders), Decimal("0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    expected = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if tender_total != expected:
        raise HTTPException(status_code=422, detail="La suma de medios de pago debe coincidir con el total de la venta")
    return tender_total


def _pos_consumer_final_customer(db: Session, *, tenant_id: UUID) -> Customer:
    """Return the one tenant-owned anonymous counterparty; never invent a DNI."""
    customer = db.scalar(select(Customer).where(
        Customer.tenant_id == tenant_id,
        Customer.code == "POS-CONSUMIDOR-FINAL",
    ).with_for_update())
    if customer is None:
        customer = Customer(
            tenant_id=tenant_id,
            code="POS-CONSUMIDOR-FINAL",
            name="Consumidor final",
            tax_id=None,
        )
        db.add(customer)
        db.flush()
    if not customer.is_active:
        raise HTTPException(status_code=409, detail="El cliente Consumidor final POS está inactivo")
    return customer


def _pos_cash_account_is_active(db: Session, *, cash_account_id: UUID, tenant_id: UUID) -> bool:
    return bool(db.execute(
        text("select 1 from cash_accounts where id=:id and tenant_id=:tenant and is_active=true and currency='PEN'"),
        {"id": str(cash_account_id), "tenant": str(tenant_id)},
    ).first())


def _resolve_price_list_prices(
    db: Session, *, tenant_id, price_list_id, lines,
) -> dict:
    """Resolve only currently-valid tier prices for a tenant-owned price list.

    This is deliberately used again by the posting command: a browser preview
    is helpful, but it must never be the authority for a commercial amount.
    """
    price_list = db.execute(text("""
        select id, code, name
        from price_lists
        where id=:price_list and tenant_id=:tenant and is_active=true
          and valid_from <= current_date
          and (valid_to is null or valid_to >= current_date)
    """), {"price_list": str(price_list_id), "tenant": str(tenant_id)}).mappings().first()
    if not price_list:
        raise HTTPException(status_code=422, detail="La lista de precios no está vigente o no pertenece a la empresa activa")

    resolved = {}
    for line in lines:
        item = db.execute(text("""
            select unit_price
            from price_list_items
            where tenant_id=:tenant and price_list_id=:price_list
              and product_id=:product and is_active=true
              and min_quantity <= :quantity
              and valid_from <= current_date
              and (valid_to is null or valid_to >= current_date)
            order by min_quantity desc, valid_from desc, id desc
            limit 1
        """), {
            "tenant": str(tenant_id), "price_list": str(price_list_id),
            "product": str(line.product_id), "quantity": line.quantity,
        }).scalar_one_or_none()
        if item is None:
            raise HTTPException(
                status_code=422,
                detail=f"La lista {price_list['code']} no tiene una tarifa vigente para uno de los productos o su cantidad",
            )
        resolved[line.product_id] = Decimal(item)
    return resolved


@router.get("/warehouses")
def list_sale_warehouses(actor: Actor = Depends(require_permission("sales:read")), db: Session = Depends(get_db)):
    """Expose only eligible warehouse identities needed when recording a sale."""
    return db.scalars(select(Warehouse).where(
        Warehouse.tenant_id == actor.tenant_id,
        Warehouse.is_active.is_(True),
    ).order_by(Warehouse.name)).all()


@router.get("/price-lists")
def list_sale_price_lists(actor: Actor = Depends(require_permission("sales:read")), db: Session = Depends(get_db)):
    """Lists commercial price lists eligible for a new sale, not historical ones."""
    return db.execute(text("""
        select l.id, l.code, l.name, c.code as currency_code, c.symbol as currency_symbol
        from price_lists l
        join currencies c on c.id=l.currency_id and c.tenant_id=l.tenant_id
        where l.tenant_id=:tenant and l.is_active=true
          and l.valid_from <= current_date
          and (l.valid_to is null or l.valid_to >= current_date)
        order by l.code, l.name
    """), {"tenant": str(actor.tenant_id)}).mappings().all()


@router.get("/payment-terms")
def list_sale_payment_terms(actor: Actor = Depends(require_permission("sales:read")), db: Session = Depends(get_db)):
    """Expose only active tenant-owned terms for the commercial posting form."""
    return db.execute(text("""
        select id, code, name, days_due
        from payment_terms
        where tenant_id=:tenant and is_active=true
        order by days_due, code, name
    """), {"tenant": str(actor.tenant_id)}).mappings().all()


@router.get("/cost-centers")
def list_sale_cost_centers(actor: Actor = Depends(require_permission("sales:read")), db: Session = Depends(get_db)):
    """Expose active tenant-owned dimensions without configuration access."""
    return db.execute(text("""
        select id, code, name from cost_centers
        where tenant_id=:tenant and is_active=true
        order by code, name
    """), {"tenant": str(actor.tenant_id)}).mappings().all()


@router.post("/price-lists/{price_list_id}/quote")
def quote_sale_price_list(
    price_list_id: UUID,
    data: PriceListQuoteCreate,
    actor: Actor = Depends(require_permission("sales:read")),
    db: Session = Depends(get_db),
):
    prices = _resolve_price_list_prices(
        db, tenant_id=actor.tenant_id, price_list_id=price_list_id, lines=data.lines,
    )
    return {
        "price_list_id": str(price_list_id),
        "lines": [
            {"product_id": str(line.product_id), "quantity": line.quantity, "unit_price": prices[line.product_id]}
            for line in data.lines
        ],
    }


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


@router.get("/{sale_id}/electronic-document/trace", response_model=ElectronicDocumentTraceOut)
def get_electronic_document_trace(sale_id: str, actor: Actor = Depends(require_permission("electronic_billing:read")), db: Session = Depends(get_db)):
    """Return tenant-scoped, immutable fiscal trace metadata without URLs."""
    _, document = _electronic_document(sale_id, actor, db)
    if not document:
        raise HTTPException(status_code=404, detail="La venta no tiene un comprobante electrónico")
    artifacts = db.scalars(select(ElectronicDocumentArtifact).where(
        ElectronicDocumentArtifact.tenant_id == actor.tenant_id,
        ElectronicDocumentArtifact.electronic_document_id == document.id,
    ).order_by(ElectronicDocumentArtifact.captured_at.desc())).all()
    events = db.scalars(select(ElectronicDocumentEvent).where(
        ElectronicDocumentEvent.tenant_id == actor.tenant_id,
        ElectronicDocumentEvent.electronic_document_id == document.id,
    ).order_by(ElectronicDocumentEvent.created_at.desc())).all()
    return {"document_id": document.id, "artifacts": artifacts, "events": events}


@router.get("/{sale_id}/electronic-document/artifacts/{artifact_id}/download", response_model=ElectronicArtifactDownloadOut)
async def download_electronic_artifact(sale_id: str, artifact_id: UUID, actor: Actor = Depends(require_permission("electronic_billing:read")), db: Session = Depends(get_db)):
    """Issue a short-lived private URL after validating sale, tenant and evidence."""
    _, document = _electronic_document(sale_id, actor, db)
    if not document:
        raise HTTPException(status_code=404, detail="La venta no tiene un comprobante electrónico")
    artifact = db.scalar(select(ElectronicDocumentArtifact).where(
        ElectronicDocumentArtifact.id == artifact_id,
        ElectronicDocumentArtifact.tenant_id == actor.tenant_id,
        ElectronicDocumentArtifact.electronic_document_id == document.id,
        ElectronicDocumentArtifact.capture_status == "stored",
    ))
    if not artifact or not artifact.storage_path:
        raise HTTPException(status_code=404, detail="El archivo fiscal solicitado no está disponible")
    return {"url": await signed_url(artifact.storage_path, expires_in=300), "expires_in": 300}


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
    settings = get_settings()
    credentials = resolve_apisunat_credentials(
        db, tenant_id=actor.tenant_id, legal_entity_id=legal_entity.id, issuer_ruc=legal_entity.tax_id,
    )
    customer = db.scalar(select(Customer).where(Customer.id == sale.customer_id, Customer.tenant_id == actor.tenant_id))
    items = db.execute(select(SaleItem, Product).join(Product, Product.id == SaleItem.product_id).where(SaleItem.sale_id == sale.id, SaleItem.tenant_id == actor.tenant_id)).all()
    if not customer or not items:
        raise HTTPException(status_code=409, detail="La venta no tiene cliente o líneas válidas para facturar")
    pos_receipt = db.scalar(select(PosReceipt).where(
        PosReceipt.sale_id == sale.id,
        PosReceipt.tenant_id == actor.tenant_id,
    ))
    customer_payload = cpe_customer_payload(
        total=sale.total,
        document_type=data.document_type,
        is_pos_receipt=pos_receipt is not None,
        customer_tax_id=customer.tax_id,
        customer_name=customer.name,
        customer_address="",
    )
    series = credentials.invoice_series if data.document_type == "01" else credentials.receipt_series
    try:
        correlative = next_correlative(
            settings, data.document_type, series,
            persona_id=credentials.persona_id, persona_token=credentials.persona_token,
        )
    except APISunatError as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    tax_rate_percent = Decimal(str(sale.tax_rate_percent if sale.tax_rate_percent is not None else "18"))
    if tax_rate_percent != Decimal("18") or sale.tax_rate_code != "IGV18":
        raise HTTPException(
            status_code=422,
            detail="La emisión CPE actual solo admite IGV 18 %. Configura un adaptador fiscal para otra afectación tributaria.",
        )
    taxable = sale.taxable_amount or (sale.total / (Decimal("1") + tax_rate_percent / Decimal("100"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    now = datetime.now(timezone.utc)
    issue_date, issue_time = now.date().isoformat(), now.strftime("%H:%M:%S")
    file_name = f"{credentials.issuer_ruc}-{data.document_type}-{series}-{correlative}"
    body = build_document_body(
        sale_number=sale.number, issue_date=issue_date, issue_time=issue_time,
        document_type=data.document_type, series=series, correlative=correlative,
        issuer={
            "ruc": legal_entity.tax_id,
            "trade_name": legal_entity.trade_name or legal_entity.legal_name,
            "legal_name": legal_entity.legal_name,
            "address": branch.address or legal_entity.fiscal_address,
        },
        customer=customer_payload,
        lines=[{"quantity": item.quantity, "unit_price": item.unit_price, "unit": product.unit_code, "description": product.name} for item, product in items],
        total=sale.total,
        tax_rate_percent=tax_rate_percent,
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
                "credential_id": str(credentials.id) if credentials.id else None,
                "credential_source": credentials.source,
                "legal_name": legal_entity.legal_name,
                "branch_name": branch.name,
            },
        },
    )
    db.add(document)
    if pos_receipt is not None:
        pos_receipt.document_type = data.document_type
    try:
        db.flush()
        record_cpe_event(
            db, document=document, event_type="reserved", actor_user_id=actor.user_id,
            payload=body, detail="Comprobante reservado para envío asíncrono",
        )
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
    document.last_error = None if document.status in {"submitted", "accepted"} else str(response.get("faults") or response.get("notes") or "APISUNAT reportó observaciones")
    capture_provider_evidence(
        db, document=document, provider_response=response,
        event_type="provider_refresh", actor_user_id=actor.user_id,
    )
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="refresh", module="electronic_billing", entity="electronic_document", entity_id=document.id)
    db.commit()
    db.refresh(document)
    return document


@router.post("/{sale_id}/electronic-document/void", response_model=ElectronicDocumentOut)
def void_electronic_document(sale_id: str, data: VoidElectronicDocument, actor: Actor = Depends(require_permission("electronic_billing:write")), _: Actor = Depends(require_second_factor), db: Session = Depends(get_db)):
    _, document = _electronic_document(sale_id, actor, db)
    if not document or not document.provider_document_id:
        raise HTTPException(status_code=404, detail="La venta no tiene un comprobante electrónico emitido")
    if document.status in {"void_pending", "voided"}:
        raise HTTPException(status_code=409, detail="El comprobante ya está en proceso de anulación o anulado")
    try:
        if not document.legal_entity_id:
            raise HTTPException(status_code=422, detail="El comprobante no tiene una entidad legal asociada")
        legal_entity = db.scalar(select(LegalEntity).where(
            LegalEntity.id == document.legal_entity_id,
            LegalEntity.tenant_id == actor.tenant_id,
        ))
        if not legal_entity:
            raise HTTPException(status_code=422, detail="No se encontró la entidad legal del comprobante")
        credentials = resolve_apisunat_credentials(
            db, tenant_id=actor.tenant_id, legal_entity_id=legal_entity.id, issuer_ruc=legal_entity.tax_id,
        )
        response = void_bill(
            get_settings(), document.provider_document_id, data.reason,
            persona_id=credentials.persona_id, persona_token=credentials.persona_token,
        )
    except APISunatError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    document.status = "void_pending"
    capture_provider_evidence(
        db, document=document, provider_response=response,
        event_type="void_requested", actor_user_id=actor.user_id,
    )
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="void", module="electronic_billing", entity="electronic_document", entity_id=document.id, detail=data.reason)
    db.commit()
    db.refresh(document)
    return document


@router.post("/pos-checkout", status_code=status.HTTP_201_CREATED)
def post_pos_checkout(
    request: Request,
    data: PosCheckoutCreate,
    actor: Actor = Depends(require_permission("sales:write", module_code="pos")),
    db: Session = Depends(get_db),
):
    """Post a retail checkout, its internal receipt and settled tender rows.

    Sale, stock issue, receivable, payment allocations, cash movements and the
    internal receipt share one database transaction.  The fiscal CPE remains a
    separately reserved asynchronous document so a provider outage cannot
    leave a cashier with an ambiguous paid sale.
    """
    idempotency = claim_idempotency(
        db,
        tenant_id=actor.tenant_id,
        actor_user_id=actor.user_id,
        operation="sales.pos_checkout",
        key=request.headers.get("Idempotency-Key"),
        payload=data.model_dump(mode="json"),
    )
    if idempotency and idempotency.is_replay:
        return replay_response(idempotency)

    if data.customer is not None:
        customer = db.scalar(select(Customer).where(
            Customer.tenant_id == actor.tenant_id,
            Customer.tax_id == data.customer.tax_id,
        ).with_for_update())
        if customer is None:
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
            record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="create_from_pos", module="customers", entity="customer", entity_id=customer.id, detail=f"Cliente creado durante POS: {data.customer.tax_id}")
        elif not customer.is_active:
            raise HTTPException(status_code=409, detail="El cliente asociado a este comprobante está inactivo")
    elif data.customer_id is not None:
        customer = db.scalar(select(Customer).where(
            Customer.id == data.customer_id,
            Customer.tenant_id == actor.tenant_id,
            Customer.is_active.is_(True),
        ))
        if customer is None:
            raise HTTPException(status_code=404, detail="Cliente no encontrado en la empresa activa")
    else:
        customer = _pos_consumer_final_customer(db, tenant_id=actor.tenant_id)

    operational_context = resolve_warehouse_context(
        db, tenant_id=actor.tenant_id, warehouse_id=data.warehouse_id,
    )
    product_ids = {line.product_id for line in data.lines}
    products_by_id = {product.id: product for product in db.scalars(select(Product).where(
        Product.tenant_id == actor.tenant_id,
        Product.id.in_(product_ids),
        Product.is_active.is_(True),
    ).with_for_update()).all()}
    if set(products_by_id) != product_ids:
        raise HTTPException(status_code=404, detail="Uno o más productos no pertenecen a la empresa activa")

    total = sum((line.quantity * products_by_id[line.product_id].sale_price for line in data.lines), Decimal("0"))
    total = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    tender_total = _validate_pos_tender_total(total=total, tenders=data.tenders)
    validate_pos_fiscal_selection(
        total=total,
        document_type=data.document_type,
        customer_tax_id=customer.tax_id,
    )

    methods = {tender.method for tender in data.tenders}
    configurations = {
        config.method: config
        for config in db.scalars(select(PosPaymentMethodConfig).where(
            PosPaymentMethodConfig.tenant_id == actor.tenant_id,
            PosPaymentMethodConfig.method.in_(methods),
            PosPaymentMethodConfig.is_active.is_(True),
        ).with_for_update()).all()
    }
    if set(configurations) != methods:
        raise HTTPException(status_code=422, detail="Configura una caja o banco activo en PEN para cada medio de pago POS")
    if any(not _pos_cash_account_is_active(
        db, cash_account_id=config.cash_account_id, tenant_id=actor.tenant_id,
    ) for config in configurations.values()):
        raise HTTPException(status_code=422, detail="Una cuenta configurada para POS ya no está activa en la empresa")

    document_date = datetime.now(timezone.utc).date()
    tax_rate = resolve_default_tax_rate(db, tenant_id=actor.tenant_id, document_date=document_date)
    taxable_amount, tax_amount = split_tax(total, tax_rate.rate)
    cost_center = resolve_cost_center(db, tenant_id=actor.tenant_id, cost_center_id=None)
    sale_number = allocate_document_number(
        db, tenant_id=actor.tenant_id, module="sales", document_type="sale",
        legal_entity_id=operational_context.legal_entity.id if operational_context.legal_entity else None,
        branch_id=operational_context.branch.id if operational_context.branch else None,
    )
    ticket_number = allocate_document_number(
        db, tenant_id=actor.tenant_id, module="pos", document_type="ticket",
        legal_entity_id=operational_context.legal_entity.id if operational_context.legal_entity else None,
        branch_id=operational_context.branch.id if operational_context.branch else None,
    )
    sale = Sale(
        tenant_id=actor.tenant_id,
        number=sale_number,
        customer_id=customer.id,
        warehouse_id=data.warehouse_id,
        due_date=document_date,
        cost_center_id=cost_center.id,
        cost_center_code=cost_center.code,
        cost_center_name=cost_center.name,
        tax_rate_id=tax_rate.id,
        tax_rate_code=tax_rate.code,
        tax_rate_name=tax_rate.name,
        tax_rate_percent=tax_rate.rate,
        taxable_amount=taxable_amount,
        tax_amount=tax_amount,
        legal_entity_id=operational_context.legal_entity.id if operational_context.legal_entity else None,
        branch_id=operational_context.branch.id if operational_context.branch else None,
        status="posted",
        total=total,
        note=data.note,
    )
    db.add(sale)
    try:
        db.flush()
        receivable = AccountReceivable(
            tenant_id=actor.tenant_id,
            sale_id=sale.id,
            customer_id=customer.id,
            original_amount=total,
            balance_amount=total,
            status="open",
            due_date=document_date,
        )
        db.add(receivable)
        db.flush()
        for line in sorted(data.lines, key=lambda item: str(item.product_id)):
            stock = db.scalar(select(Inventory).where(
                Inventory.tenant_id == actor.tenant_id,
                Inventory.product_id == line.product_id,
                Inventory.warehouse_id == data.warehouse_id,
            ).with_for_update())
            reserved = db.execute(text("""
                select coalesce(sum(quantity),0) from stock_reservations
                where tenant_id=:tenant and product_id=:product and warehouse_id=:warehouse and status='active'
            """), {"tenant": str(actor.tenant_id), "product": str(line.product_id), "warehouse": str(data.warehouse_id)}).scalar() or Decimal("0")
            if stock is None or Decimal(str(stock.quantity)) - Decimal(str(reserved)) < line.quantity:
                raise HTTPException(status_code=422, detail="No hay stock disponible suficiente; parte de las existencias está reservada")
        for line in data.lines:
            product = products_by_id[line.product_id]
            line_total = (line.quantity * product.sale_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            db.add(SaleItem(
                tenant_id=actor.tenant_id,
                sale_id=sale.id,
                product_id=product.id,
                quantity=line.quantity,
                unit_price=product.sale_price,
                line_total=line_total,
            ))
            issue_tracked_stock(
                db,
                tenant_id=actor.tenant_id,
                product=product,
                warehouse_id=data.warehouse_id,
                quantity=line.quantity,
                reference=sale_number,
                serial_numbers=line.serial_numbers,
            )
        db.add(PosReceipt(
            tenant_id=actor.tenant_id,
            sale_id=sale.id,
            ticket_number=ticket_number,
            document_type=data.document_type,
            customer_identified=bool(customer.tax_id),
            tender_total=tender_total,
        ))
        post_entry(
            db,
            tenant_id=actor.tenant_id,
            actor_id=actor.user_id,
            number=f"AS-{sale_number}",
            description=f"Venta POS {sale_number}",
            source_type="sale",
            source_id=sale.id,
            lines=[("1210", total, Decimal("0"), cost_center.id), ("7010", Decimal("0"), taxable_amount, cost_center.id), ("4010", Decimal("0"), tax_amount, cost_center.id)],
        )
        payment_numbers: list[str] = []
        for index, tender in enumerate(data.tenders, start=1):
            config = configurations[tender.method]
            payment_number = f"{ticket_number}-{index}"
            payment = Payment(
                tenant_id=actor.tenant_id,
                number=payment_number,
                payment_type="receipt",
                cash_account_id=config.cash_account_id,
                customer_id=customer.id,
                amount=tender.amount,
                method=tender.method,
                reference=tender.reference or None,
                note=f"Cobro POS {ticket_number}",
                status="posted",
            )
            db.add(payment)
            db.flush()
            receivable.balance_amount -= tender.amount
            db.add(PaymentAllocation(
                tenant_id=actor.tenant_id,
                payment_id=payment.id,
                receivable_id=receivable.id,
                amount=tender.amount,
            ))
            db.execute(text("""
                insert into cash_movements(tenant_id,cash_account_id,payment_id,movement_type,amount,reference)
                values(:tenant,:account,:payment,'inflow',:amount,:reference)
            """), {"tenant": str(actor.tenant_id), "account": str(config.cash_account_id), "payment": str(payment.id), "amount": tender.amount, "reference": payment_number})
            post_entry(
                db,
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                number=f"AS-{payment_number}",
                description=f"Cobro POS {ticket_number}",
                source_type="payment",
                source_id=payment.id,
                lines=[("1010", tender.amount, Decimal("0")), ("1210", Decimal("0"), tender.amount)],
            )
            payment_numbers.append(payment_number)
        receivable.status = "paid" if receivable.balance_amount == 0 else "partial"
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="post_pos", module="pos", entity="sale", entity_id=sale.id, detail=ticket_number)
        response_body = {
            "sale_id": str(sale.id),
            "sale_number": sale.number,
            "ticket_number": ticket_number,
            "document_type": data.document_type,
            "customer_id": str(customer.id),
            "customer_identified": bool(customer.tax_id),
            "total": total,
            "tender_total": tender_total,
            "balance_amount": Decimal("0.00"),
            "payment_numbers": payment_numbers,
            "electronic_document_status": "not_requested" if data.document_type == "ticket" else "pending_reservation",
        }
        complete_idempotency(db, claim=idempotency, response_body=response_body, response_status=status.HTTP_201_CREATED)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="No se pudo completar el cobro POS; vuelve a intentarlo con la misma clave") from exc
    return response_body


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
    customer = db.scalar(select(Customer).where(Customer.id == customer_id, Customer.tenant_id == actor.tenant_id, Customer.is_active.is_(True)))
    operational_context = resolve_warehouse_context(
        db, tenant_id=actor.tenant_id, warehouse_id=data.warehouse_id,
    )
    product_ids = {line.product_id for line in data.lines}
    products = set(db.scalars(select(Product.id).where(Product.tenant_id == actor.tenant_id, Product.id.in_(product_ids), Product.is_active.is_(True))).all())
    if not customer or products != product_ids:
        raise HTTPException(status_code=404, detail="Cliente, almacén o producto no pertenece a la empresa activa")
    validate_administrative_sale_customer(customer_tax_id=customer.tax_id)
    resolved_prices = (
        _resolve_price_list_prices(
            db, tenant_id=actor.tenant_id, price_list_id=data.price_list_id, lines=data.lines,
        )
        if data.price_list_id else {}
    )
    total = sum((
        line.quantity * resolved_prices.get(line.product_id, line.unit_price)
        for line in data.lines
    ), Decimal("0"))
    document_date = datetime.now(timezone.utc).date()
    tax_rate = resolve_default_tax_rate(
        db, tenant_id=actor.tenant_id, document_date=document_date,
    )
    taxable_amount, tax_amount = split_tax(total, tax_rate.rate)
    payment_term, due_date = resolve_payment_term(
        db,
        tenant_id=actor.tenant_id,
        payment_term_id=data.payment_term_id,
        supplied_due_date=data.due_date,
        document_date=document_date,
    )
    cost_center = resolve_cost_center(
        db, tenant_id=actor.tenant_id, cost_center_id=data.cost_center_id,
    )
    number = allocate_document_number(
        db, tenant_id=actor.tenant_id, module="sales", document_type="sale",
        legal_entity_id=operational_context.legal_entity.id if operational_context.legal_entity else None,
        branch_id=operational_context.branch.id if operational_context.branch else None,
    )
    sale = Sale(
        tenant_id=actor.tenant_id, number=number, customer_id=customer.id,
        warehouse_id=data.warehouse_id,
        price_list_id=data.price_list_id,
        payment_term_id=payment_term.id if payment_term else None,
        payment_term_code=payment_term.code if payment_term else None,
        payment_term_name=payment_term.name if payment_term else None,
        payment_term_days_due=payment_term.days_due if payment_term else None,
        due_date=due_date,
        cost_center_id=cost_center.id,
        cost_center_code=cost_center.code,
        cost_center_name=cost_center.name,
        tax_rate_id=tax_rate.id,
        tax_rate_code=tax_rate.code,
        tax_rate_name=tax_rate.name,
        tax_rate_percent=tax_rate.rate,
        taxable_amount=taxable_amount,
        tax_amount=tax_amount,
        legal_entity_id=operational_context.legal_entity.id if operational_context.legal_entity else None,
        branch_id=operational_context.branch.id if operational_context.branch else None,
        status="posted", total=total, note=data.note,
    )
    db.add(sale)
    try:
        db.flush()
        db.add(AccountReceivable(tenant_id=actor.tenant_id, sale_id=sale.id, customer_id=customer.id, original_amount=total, balance_amount=total, status="open", due_date=due_date))
        products_by_id = {product.id: product for product in db.scalars(select(Product).where(
            Product.tenant_id == actor.tenant_id,
            Product.id.in_(product_ids),
        ).with_for_update()).all()}
        stock_already_issued = bool(getattr(request.state, "stock_already_issued_by_delivery", False))
        own_order_id = getattr(request.state, "sales_order_reservation_id", None)
        if not stock_already_issued:
            # Direct sales must not consume stock already promised to a
            # different confirmed order. A conversion from its own order may
            # consume that order's reservation, but never another one's.
            for line in sorted(data.lines, key=lambda item: str(item.product_id)):
                stock = db.scalar(select(Inventory).where(
                    Inventory.tenant_id == actor.tenant_id,
                    Inventory.product_id == line.product_id,
                    Inventory.warehouse_id == data.warehouse_id,
                ).with_for_update())
                reservation_filter = ""
                reservation_params = {
                    "tenant": str(actor.tenant_id),
                    "product": str(line.product_id),
                    "warehouse": str(data.warehouse_id),
                }
                if own_order_id:
                    reservation_filter = """
                      and not (reference_type='sales_order' and reference_id=:own_order)
                    """
                    reservation_params["own_order"] = str(own_order_id)
                reserved = db.execute(text("""
                    select coalesce(sum(quantity),0) from stock_reservations
                    where tenant_id=:tenant and product_id=:product and warehouse_id=:warehouse and status='active'
                """ + reservation_filter), reservation_params).scalar() or Decimal("0")
                if stock is None or Decimal(str(stock.quantity)) - Decimal(str(reserved)) < line.quantity:
                    raise HTTPException(status_code=422, detail="No hay stock disponible suficiente; parte de las existencias está reservada")
        for line in data.lines:
            unit_price = resolved_prices.get(line.product_id, line.unit_price)
            db.add(SaleItem(tenant_id=actor.tenant_id, sale_id=sale.id, product_id=line.product_id, quantity=line.quantity, unit_price=unit_price, line_total=line.quantity * unit_price))
            if not stock_already_issued:
                issue_tracked_stock(
                    db, tenant_id=actor.tenant_id, product=products_by_id[line.product_id],
                    warehouse_id=data.warehouse_id, quantity=line.quantity, reference=number,
                    serial_numbers=line.serial_numbers,
                )
        post_entry(db, tenant_id=actor.tenant_id, actor_id=actor.user_id, number=f"AS-{number}", description=f"Venta {number}", source_type="sale", source_id=sale.id, lines=[("1210", total, Decimal("0"), cost_center.id), ("7010", Decimal("0"), taxable_amount, cost_center.id), ("4010", Decimal("0"), tax_amount, cost_center.id)])
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
    sales_order_id = db.execute(text("""
        select sales_order_id from sales where id=:id and tenant_id=:tenant
    """), {"id": str(sale.id), "tenant": str(actor.tenant_id)}).scalar_one_or_none()
    delivered_items = db.execute(text("""
        select d.number as delivery_number, i.product_id, i.quantity
        from sales_deliveries d
        join sales_delivery_items i on i.delivery_id=d.id and i.tenant_id=d.tenant_id
        where d.tenant_id=:tenant and d.sales_order_id=:order and d.status in ('dispatched','delivered')
        order by d.created_at, d.id, i.id
    """), {"tenant": str(actor.tenant_id), "order": str(sales_order_id)}).mappings().all() if sales_order_id else []
    reversal_lines = delivered_items or [
        {"delivery_number": sale.number, "product_id": item.product_id, "quantity": item.quantity}
        for item in db.scalars(select(SaleItem).where(SaleItem.sale_id == sale.id, SaleItem.tenant_id == actor.tenant_id)).all()
    ]
    for item in reversal_lines:
        product = db.scalar(select(Product).where(
            Product.id == item["product_id"],
            Product.tenant_id == actor.tenant_id,
        ).with_for_update())
        if not product:
            raise HTTPException(status_code=409, detail="No se encontró el producto de una línea de venta")
        return_tracked_sale_stock(
            db, tenant_id=actor.tenant_id, product=product, warehouse_id=sale.warehouse_id,
            quantity=Decimal(str(item["quantity"])), sale_reference=item["delivery_number"],
            reversal_reference=f"CANCEL-{sale.number}-{item['delivery_number']}",
        )
    sale.status = "cancelled"
    account.status = "cancelled"
    reverse_entry(db, tenant_id=actor.tenant_id, actor_id=actor.user_id, number=f"REV-{sale.number}", description=f"Reversión de venta {sale.number}", original_source_type="sale", original_source_id=sale.id, reversal_source_type="sale_cancel")
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="cancel", module="sales", entity="sale", entity_id=sale.id)
    db.commit()
    db.refresh(sale)
    return sale


@router.post("/{sale_id}/return", response_model=CommercialReturnOut, status_code=status.HTTP_201_CREATED)
def return_sale(sale_id: str, data: CommercialReturnCreate, request: Request, actor: Actor = Depends(require_permission("sales:write")), db: Session = Depends(get_db)):
    """Register a full operational return and reverse its evidence.

    A live CPE must be handled with a linked fiscal credit note; this endpoint
    refuses to make an operational-only return look like a legal reversal.
    """
    claim = claim_idempotency(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id,
        operation=f"sales.return.{sale_id}", key=request.headers.get("Idempotency-Key"),
        payload={"sale_id": sale_id, **data.model_dump(mode="json")})
    if claim and claim.is_replay:
        return replay_response(claim)
    sale = db.scalar(select(Sale).where(Sale.id == sale_id, Sale.tenant_id == actor.tenant_id).with_for_update())
    account = db.scalar(select(AccountReceivable).where(AccountReceivable.sale_id == sale_id, AccountReceivable.tenant_id == actor.tenant_id).with_for_update())
    if not sale or sale.status != "posted" or not account:
        raise HTTPException(status_code=409, detail="Solo se pueden devolver ventas procesadas vigentes")
    if account.balance_amount != account.original_amount:
        raise HTTPException(status_code=409, detail="No se puede devolver una venta con cobros aplicados")
    electronic_document = db.scalar(select(ElectronicDocument).where(ElectronicDocument.sale_id == sale.id, ElectronicDocument.tenant_id == actor.tenant_id))
    if electronic_document and electronic_document.status not in {"voided", "rejected", "exception"}:
        raise HTTPException(status_code=409, detail="La venta tiene un CPE activo: emite una nota de crédito antes de registrar la devolución")
    if db.execute(text("select 1 from sales_returns where tenant_id=:tenant and sale_id=:sale"), {"tenant": str(actor.tenant_id), "sale": sale_id}).first():
        raise HTTPException(status_code=409, detail="La venta ya tiene una devolución registrada")
    sales_order_id = db.execute(text("select sales_order_id from sales where id=:id and tenant_id=:tenant"), {"id": sale_id, "tenant": str(actor.tenant_id)}).scalar_one_or_none()
    delivered_items = db.execute(text("""
        select d.number as delivery_number, i.product_id, i.quantity
        from sales_deliveries d join sales_delivery_items i on i.delivery_id=d.id and i.tenant_id=d.tenant_id
        where d.tenant_id=:tenant and d.sales_order_id=:order and d.status in ('dispatched','delivered')
        order by d.created_at, d.id, i.id
    """), {"tenant": str(actor.tenant_id), "order": str(sales_order_id)}).mappings().all() if sales_order_id else []
    reversal_lines = delivered_items or [{"delivery_number": sale.number, "product_id": item.product_id, "quantity": item.quantity} for item in db.scalars(select(SaleItem).where(SaleItem.sale_id == sale.id, SaleItem.tenant_id == actor.tenant_id)).all()]
    number = allocate_document_number(db, tenant_id=actor.tenant_id, module="sales", document_type="sale_return", legal_entity_id=sale.legal_entity_id, branch_id=sale.branch_id)
    try:
        for item in reversal_lines:
            product = db.scalar(select(Product).where(Product.id == item["product_id"], Product.tenant_id == actor.tenant_id).with_for_update())
            if not product:
                raise HTTPException(status_code=409, detail="No se encontró un producto de la venta")
            return_tracked_sale_stock(db, tenant_id=actor.tenant_id, product=product, warehouse_id=sale.warehouse_id, quantity=Decimal(str(item["quantity"])), sale_reference=item["delivery_number"], reversal_reference=number)
        returned_id = db.execute(text("""
            insert into sales_returns(tenant_id,sale_id,number,reason,status,created_by)
            values(:tenant,:sale,:number,:reason,'posted',:actor) returning id
        """), {"tenant": str(actor.tenant_id), "sale": sale_id, "number": number, "reason": data.reason.strip(), "actor": str(actor.user_id)}).scalar_one()
        sale.status, account.status = "returned", "cancelled"
        reverse_entry(db, tenant_id=actor.tenant_id, actor_id=actor.user_id, number=f"REV-{number}", description=f"Devolución de venta {sale.number}", original_source_type="sale", original_source_id=sale.id, reversal_source_type="sale_return")
        result = {"id": returned_id, "sale_id": sale.id, "number": number, "reason": data.reason.strip(), "status": "posted"}
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="return", module="sales", entity="sale_return", entity_id=returned_id, detail=sale.number)
        complete_idempotency(db, claim=claim, response_body=CommercialReturnOut.model_validate(result).model_dump(mode="json"), response_status=status.HTTP_201_CREATED)
        db.commit()
        return result
    except HTTPException:
        db.rollback()
        raise
