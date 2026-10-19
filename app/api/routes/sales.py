from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.core.config import get_settings
from app.models.entities import AccountReceivable, Customer, ElectronicDocument, Product, Sale, SaleItem, Warehouse
from app.schemas.electronic_billing import ElectronicDocumentCreate, ElectronicDocumentOut, VoidElectronicDocument
from app.schemas.transactions import CancelTransaction, SaleCreate, SaleOut
from app.security.auth import Actor, require_permission
from app.services.audit import record
from app.services.apisunat import APISunatError, build_document_body, get_document, next_correlative, send_bill, void_bill
from app.services.stock import change_stock
from app.services.decolecta import DecolectaError, lookup_tax_id

router = APIRouter(prefix="/sales", tags=["sales"])


@router.get("/warehouses")
def list_sale_warehouses(actor: Actor = Depends(require_permission("sales:read")), db: Session = Depends(get_db)):
    """Expose only eligible warehouse identities needed when recording a sale."""
    return db.scalars(select(Warehouse).where(
        Warehouse.tenant_id == actor.tenant_id,
        Warehouse.is_active.is_(True),
    ).order_by(Warehouse.name)).all()


@router.get("", response_model=list[SaleOut])
def list_sales(actor: Actor = Depends(require_permission("sales:read")), db: Session = Depends(get_db)):
    return db.scalars(select(Sale).where(Sale.tenant_id == actor.tenant_id).order_by(Sale.created_at.desc())).all()


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


def _provider_status(value: object) -> str:
    mapping = {"PENDIENTE": "pending", "ACEPTADO": "accepted", "RECHAZADO": "rejected", "EXCEPCION": "exception"}
    return mapping.get(str(value).upper(), "error")


@router.get("/{sale_id}/electronic-document", response_model=ElectronicDocumentOut | None)
def get_electronic_document(sale_id: str, actor: Actor = Depends(require_permission("electronic_billing:read")), db: Session = Depends(get_db)):
    _, document = _electronic_document(sale_id, actor, db)
    return document


@router.post("/{sale_id}/electronic-document", response_model=ElectronicDocumentOut, status_code=status.HTTP_201_CREATED)
def issue_electronic_document(sale_id: str, data: ElectronicDocumentCreate, actor: Actor = Depends(require_permission("electronic_billing:write")), db: Session = Depends(get_db)):
    sale, existing = _electronic_document(sale_id, actor, db)
    if sale.status != "posted":
        raise HTTPException(status_code=409, detail="Solo se pueden emitir comprobantes de ventas procesadas")
    if existing:
        raise HTTPException(status_code=409, detail="Esta venta ya tiene un comprobante electrónico")
    customer = db.scalar(select(Customer).where(Customer.id == sale.customer_id, Customer.tenant_id == actor.tenant_id))
    items = db.execute(select(SaleItem, Product).join(Product, Product.id == SaleItem.product_id).where(SaleItem.sale_id == sale.id, SaleItem.tenant_id == actor.tenant_id)).all()
    if not customer or not items:
        raise HTTPException(status_code=409, detail="La venta no tiene cliente o líneas válidas para facturar")
    tax_id = (customer.tax_id or "").strip()
    if data.document_type == "01" and (len(tax_id) != 11 or not tax_id.isdigit()):
        raise HTTPException(status_code=422, detail="Para emitir Factura el cliente debe tener un RUC de 11 dígitos")
    if data.document_type == "03" and (len(tax_id) not in {8, 11} or not tax_id.isdigit()):
        raise HTTPException(status_code=422, detail="Para emitir Boleta el cliente debe tener DNI de 8 dígitos o RUC de 11 dígitos")
    settings = get_settings()
    series = settings.apisunat_invoice_series if data.document_type == "01" else settings.apisunat_receipt_series
    try:
        correlative = next_correlative(settings, data.document_type, series)
        now = datetime.now(timezone.utc)
        issue_date, issue_time = now.date().isoformat(), now.strftime("%H:%M:%S")
        body = build_document_body(
            sale_number=sale.number, issue_date=issue_date, issue_time=issue_time, document_type=data.document_type, series=series, correlative=correlative,
            issuer={"ruc": settings.apisunat_issuer_ruc or "", "trade_name": settings.apisunat_issuer_trade_name or "", "legal_name": settings.apisunat_issuer_legal_name or "", "address": settings.apisunat_issuer_address or ""},
            customer={"tax_id": tax_id, "name": customer.name, "address": ""},
            lines=[{"quantity": item.quantity, "unit_price": item.unit_price, "unit": product.unit, "description": product.name} for item, product in items],
            total=sale.total,
        )
        file_name = f"{settings.apisunat_issuer_ruc}-{data.document_type}-{series}-{correlative}"
        response = send_bill(settings, {"personaId": settings.apisunat_persona_id, "personaToken": settings.apisunat_persona_token, "fileName": file_name, "documentBody": body, **({"customerEmail": customer.email} if customer.email else {})})
        if not response.get("documentId"):
            raise APISunatError("APISUNAT no devolvió el identificador del comprobante")
    except APISunatError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    taxable = (sale.total / Decimal("1.18")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    document = ElectronicDocument(tenant_id=actor.tenant_id, sale_id=sale.id, document_type=data.document_type, series=series, correlative=correlative, file_name=file_name, status=_provider_status(response.get("status")), provider_document_id=response.get("documentId"), taxable_amount=taxable, tax_amount=sale.total - taxable, total_amount=sale.total, provider_payload=response)
    db.add(document)
    db.flush()
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="issue", module="electronic_billing", entity="electronic_document", entity_id=document.id, detail=f"{file_name} · {response.get('status', 'PENDIENTE')}")
    db.commit()
    db.refresh(document)
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
    document.status = _provider_status(response.get("status"))
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
def post_sale(data: SaleCreate, actor: Actor = Depends(require_permission("sales:write")), db: Session = Depends(get_db)):
    customer = db.scalar(select(Customer.id).where(Customer.id == data.customer_id, Customer.tenant_id == actor.tenant_id, Customer.is_active.is_(True)))
    warehouse = db.scalar(select(Warehouse.id).where(Warehouse.id == data.warehouse_id, Warehouse.tenant_id == actor.tenant_id, Warehouse.is_active.is_(True)))
    product_ids = {line.product_id for line in data.lines}
    products = set(db.scalars(select(Product.id).where(Product.tenant_id == actor.tenant_id, Product.id.in_(product_ids), Product.is_active.is_(True))).all())
    if not customer or not warehouse or products != product_ids:
        raise HTTPException(status_code=404, detail="Cliente, almacén o producto no pertenece a la empresa activa")
    total = sum((line.quantity * line.unit_price for line in data.lines), Decimal("0"))
    sale = Sale(tenant_id=actor.tenant_id, number=data.number, customer_id=data.customer_id, warehouse_id=data.warehouse_id, status="posted", total=total, note=data.note)
    db.add(sale)
    try:
        db.flush()
        db.add(AccountReceivable(tenant_id=actor.tenant_id, sale_id=sale.id, customer_id=data.customer_id, original_amount=total, balance_amount=total, status="open"))
        for line in data.lines:
            db.add(SaleItem(tenant_id=actor.tenant_id, sale_id=sale.id, product_id=line.product_id, quantity=line.quantity, unit_price=line.unit_price, line_total=line.quantity * line.unit_price))
            change_stock(db, tenant_id=actor.tenant_id, product_id=line.product_id, warehouse_id=data.warehouse_id, quantity=-line.quantity, movement_type="issue", reference=data.number)
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="post", module="sales", entity="sale", entity_id=sale.id)
        db.commit()
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
        change_stock(db, tenant_id=actor.tenant_id, product_id=item.product_id, warehouse_id=sale.warehouse_id, quantity=item.quantity, movement_type="receipt", reference=f"CANCEL-{sale.number}")
    sale.status = "cancelled"
    account.status = "cancelled"
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="cancel", module="sales", entity="sale", entity_id=sale.id)
    db.commit()
    db.refresh(sale)
    return sale
