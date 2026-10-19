"""Secure, tenant-scoped RAG export and n8n sync notifications."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from datetime import datetime
from decimal import Decimal
from secrets import compare_digest
from typing import Any
from uuid import UUID

import httpx
from fastapi import Header, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def require_rag_integration_key(
    x_rag_integration_key: str | None = Header(default=None),
) -> None:
    """Authenticate machine-to-machine RAG reads without a browser JWT."""
    configured = get_settings().rag_integration_key
    # ``secrets.compare_digest`` only accepts text made exclusively of ASCII
    # characters. Comparing encoded values preserves its constant-time behavior
    # for any valid secret and turns a malformed key into a normal 401 response
    # instead of leaking an internal 500 error to n8n.
    matches = (
        bool(configured and x_rag_integration_key)
        and compare_digest(
            configured.encode("utf-8"),
            x_rag_integration_key.encode("utf-8"),
        )
    )
    if not matches:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credencial de integración RAG inválida")


def _value(value: Any) -> Any:
    if isinstance(value, (UUID, datetime)):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


def _document(*, tenant_id: UUID, source: str, record_id: Any, title: str, content: str, updated_at: Any = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": f"{source}:{record_id}",
        "tenant_id": str(tenant_id),
        "source": source,
        "title": title,
        "content": content,
        "updated_at": _value(updated_at),
        "metadata": {"source": source, **(metadata or {})},
    }


def export_tenant_documents(db: Session, tenant_id: UUID) -> list[dict[str, Any]]:
    """Return business context suitable for embeddings, scoped to one company.

    Contact emails, tax IDs, passwords, documents and user identities are deliberately
    excluded. The RAG assistant receives only operational facts needed to answer ERP
    questions, and every row keeps its tenant identity for vector filtering.
    """
    tenant = db.execute(text("select id, name, timezone from tenants where id = :tenant and is_active = true"), {"tenant": str(tenant_id)}).mappings().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Empresa no encontrada o inactiva")

    documents: list[dict[str, Any]] = [
        _document(
            tenant_id=tenant_id,
            source="erp_guide",
            record_id="capabilities",
            title="Guía operativa de EnterpriseCloud",
            content=(
                f"Empresa: {tenant['name']}. EnterpriseCloud permite gestionar ventas, compras, "
                "inventario, clientes, proveedores, tesorería, conciliación, documentos y reportes. "
                "El asistente puede explicar información operativa disponible, pero no ejecuta cambios, "
                "no revela credenciales y no sustituye validaciones contables."
            ),
            metadata={"tenant_name": tenant["name"], "timezone": tenant["timezone"]},
        )
    ]

    product_rows = db.execute(text("""
        select p.id, p.sku, p.name, p.unit, p.sale_price, p.is_active, p.updated_at,
               c.name as category_name, coalesce(sum(i.quantity), 0) as stock_units
        from products p
        left join categories c on c.id = p.category_id and c.tenant_id = p.tenant_id
        left join inventories i on i.product_id = p.id and i.tenant_id = p.tenant_id
        where p.tenant_id = :tenant
        group by p.id, c.name
        order by p.name
    """), {"tenant": str(tenant_id)}).mappings()
    for row in product_rows:
        documents.append(_document(
            # Catalogue facts and stock are intentionally separate documents.
            # A commercial role may consult a price without the vector search
            # ever receiving inventory quantities for that role.
            tenant_id=tenant_id, source="product_catalog", record_id=row["id"], title=f"Producto: {row['name']}",
            content=(f"Producto {row['name']} (SKU {row['sku']}). Categoría: {row['category_name'] or 'Sin categoría'}. "
                     f"Unidad: {row['unit']}. Precio de venta: S/ {row['sale_price']}. "
                     f"Estado: {'activo' if row['is_active'] else 'inactivo'} ."),
            updated_at=row["updated_at"], metadata={"sku": row["sku"], "category": row["category_name"]},
        ))
        documents.append(_document(
            tenant_id=tenant_id, source="inventory_stock", record_id=row["id"], title=f"Existencias: {row['name']}",
            content=(f"Inventario del producto {row['name']} (SKU {row['sku']}). "
                     f"Stock disponible: {row['stock_units']}. Unidad: {row['unit']}. "
                     f"Estado del producto: {'activo' if row['is_active'] else 'inactivo'}."),
            updated_at=row["updated_at"], metadata={"sku": row["sku"], "stock_units": _value(row["stock_units"])},
        ))

    for source, table, label in (("customer", "customers", "Cliente"), ("supplier", "suppliers", "Proveedor"), ("warehouse", "warehouses", "Almacén")):
        rows = db.execute(text(f"select id, code, name, is_active, updated_at from {table} where tenant_id = :tenant order by name"), {"tenant": str(tenant_id)}).mappings()
        for row in rows:
            documents.append(_document(
                tenant_id=tenant_id, source=source, record_id=row["id"], title=f"{label}: {row['name']}",
                content=f"{label} {row['name']}. Código: {row['code']}. Estado: {'activo' if row['is_active'] else 'inactivo'}.",
                updated_at=row["updated_at"], metadata={"code": row["code"], "active": row["is_active"]},
            ))

    transaction_specs = (
        ("sale", "sales", "customer", "customers", "Venta", "customer_name"),
        ("purchase", "purchases", "supplier", "suppliers", "Compra", "supplier_name"),
    )
    for source, table, party_column, party_table, label, party_alias in transaction_specs:
        rows = db.execute(text(f"""
            select t.id, t.number, t.status, t.total, t.note, t.created_at, t.updated_at, p.name as {party_alias}
            from {table} t join {party_table} p on p.id = t.{party_column}_id
            where t.tenant_id = :tenant order by t.created_at desc limit 500
        """), {"tenant": str(tenant_id)}).mappings()
        for row in rows:
            documents.append(_document(
                tenant_id=tenant_id, source=source, record_id=row["id"], title=f"{label} {row['number']}",
                content=(f"{label} número {row['number']}. {'Cliente' if source == 'sale' else 'Proveedor'}: {row[party_alias]}. "
                         f"Estado: {row['status']}. Total: S/ {row['total']}. Nota: {row['note'] or 'Sin nota'} ."),
                updated_at=row["updated_at"], metadata={"number": row["number"], "status": row["status"], "total": _value(row["total"]), "created_at": _value(row["created_at"])},
            ))

    for source, table, party_table, party_key, label in (
        ("receivable", "accounts_receivable", "customers", "customer_id", "Cuenta por cobrar"),
        ("payable", "accounts_payable", "suppliers", "supplier_id", "Cuenta por pagar"),
    ):
        rows = db.execute(text(f"""
            select a.id, a.status, a.original_amount, a.balance_amount, a.updated_at, p.name as party_name
            from {table} a join {party_table} p on p.id = a.{party_key}
            where a.tenant_id = :tenant and a.status in ('open', 'partial')
            order by a.created_at desc limit 500
        """), {"tenant": str(tenant_id)}).mappings()
        for row in rows:
            documents.append(_document(
                tenant_id=tenant_id, source=source, record_id=row["id"], title=f"{label}: {row['party_name']}",
                content=(f"{label} de {row['party_name']}. Estado: {row['status']}. "
                         f"Monto original: S/ {row['original_amount']}. Saldo pendiente: S/ {row['balance_amount']} ."),
                updated_at=row["updated_at"], metadata={"status": row["status"], "balance": _value(row["balance_amount"])},
            ))
    return documents


def queue_sync_event(db: Session, *, tenant_id: UUID, module: str, entity: str, entity_id: UUID | None, action: str) -> None:
    """Queue an outbound event; Session listeners send it only after commit."""
    db.info.setdefault("rag_sync_events", []).append({
        "tenant_id": str(tenant_id), "module": module, "entity": entity,
        "entity_id": str(entity_id) if entity_id else None, "action": action,
        "occurred_at": datetime.utcnow().isoformat() + "Z",
    })


def signed_request(payload: dict[str, Any], secret: str) -> tuple[bytes, dict[str, str]]:
    """Create replay-resistant HMAC headers understood by both n8n workflows."""
    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return body, {
        "Content-Type": "application/json",
        "X-ERP-Timestamp": timestamp,
        "X-ERP-Signature": f"sha256={signature}",
    }


def dispatch_sync_event(event: dict[str, Any]) -> None:
    settings = get_settings()
    if not settings.rag_sync_webhook_url or not settings.rag_sync_webhook_secret:
        return
    try:
        with httpx.Client(timeout=5) as client:
            body, headers = signed_request(event, settings.rag_sync_webhook_secret)
            client.post(
                str(settings.rag_sync_webhook_url),
                content=body,
                headers=headers,
            ).raise_for_status()
    except Exception:
        logger.warning("rag_sync_webhook_failed tenant_id=%s module=%s", event["tenant_id"], event["module"], exc_info=True)


def dispatch_after_commit(session: Session) -> None:
    events = session.info.pop("rag_sync_events", [])
    for event in events:
        threading.Thread(target=dispatch_sync_event, args=(event,), daemon=True).start()


def clear_after_rollback(session: Session) -> None:
    session.info.pop("rag_sync_events", None)
