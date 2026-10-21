"""Immutable evidence records used by the approval and signature workflows.

The ERP owns the canonical operational payload and its SHA-256 digest.  A
digital-signature provider may later sign that digest, but private certificates,
PINs and provider credentials must remain outside this service and database.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def _json_default(value: Any) -> str:
    if isinstance(value, (UUID, date, datetime)):
        return str(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(f"Unsupported evidence value: {type(value)!r}")


def canonicalize(payload: dict[str, Any]) -> str:
    """Return the stable representation that is hashed and retained as evidence."""
    return json.dumps(payload, default=_json_default, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(payload: dict[str, Any]) -> str:
    return sha256(canonicalize(payload).encode("utf-8")).hexdigest()


def create_inventory_count_evidence(
    db: Session,
    *,
    tenant_id: UUID,
    count_id: UUID,
    approval_request_id: UUID | None,
    requested_by: UUID,
) -> dict[str, Any]:
    """Create the immutable snapshot shown to the approver for a physical count."""
    header = db.execute(text("""
        select c.id, c.number, c.count_date, c.reason, c.note, c.snapshot_at,
               w.code as warehouse_code, w.name as warehouse_name
        from inventory_counts c
        join warehouses w on w.id=c.warehouse_id and w.tenant_id=c.tenant_id
        where c.id=:count_id and c.tenant_id=:tenant
    """), {"count_id": str(count_id), "tenant": str(tenant_id)}).mappings().one()
    lines = db.execute(text("""
        select p.sku, p.name as product_name, l.expected_quantity, l.counted_quantity, l.note
        from inventory_count_lines l
        join products p on p.id=l.product_id and p.tenant_id=l.tenant_id
        where l.inventory_count_id=:count_id and l.tenant_id=:tenant
        order by p.sku, p.name
    """), {"count_id": str(count_id), "tenant": str(tenant_id)}).mappings().all()
    payload = {
        "schema": "enterprisecloud.approval-document.v1",
        "document_type": "inventory_physical_count",
        "number": header["number"],
        "count_date": header["count_date"],
        "snapshot_at": header["snapshot_at"],
        "warehouse": {"code": header["warehouse_code"], "name": header["warehouse_name"]},
        "reason": header["reason"],
        "note": header["note"],
        "lines": [dict(line) for line in lines],
    }
    serialized = canonicalize(payload)
    digest = sha256(serialized.encode("utf-8")).hexdigest()
    return db.execute(text("""
        insert into approval_documents(
          tenant_id, approval_request_id, module, entity_type, entity_id,
          document_number, canonical_payload, content_hash, requested_by
        ) values (
          :tenant, :request, 'inventory', 'inventory_count', :count_id,
          :number, cast(:payload as jsonb), :digest, :requested_by
        )
        returning *
    """), {
        "tenant": str(tenant_id), "request": str(approval_request_id) if approval_request_id else None,
        "count_id": str(count_id), "number": f"INV-FIS-{header['number']}",
        "payload": serialized, "digest": digest, "requested_by": str(requested_by),
    }).mappings().one()


def create_purchase_order_evidence(
    db: Session,
    *,
    tenant_id: UUID,
    order_id: UUID,
    approval_request_id: UUID,
    requested_by: UUID,
) -> dict[str, Any]:
    """Create the immutable request snapshot for a purchase order."""
    header = db.execute(text("""
        select o.number, o.total, s.code as supplier_code, s.name as supplier_name,
               s.tax_id as supplier_tax_id, w.code as warehouse_code, w.name as warehouse_name
        from purchase_orders o
        join suppliers s on s.id=o.supplier_id and s.tenant_id=o.tenant_id
        join warehouses w on w.id=o.warehouse_id and w.tenant_id=o.tenant_id
        where o.id=:order_id and o.tenant_id=:tenant
    """), {"order_id": str(order_id), "tenant": str(tenant_id)}).mappings().one()
    lines = db.execute(text("""
        select p.sku, p.name as product_name, p.unit, l.quantity, l.unit_cost, l.line_total
        from purchase_order_items l
        join products p on p.id=l.product_id and p.tenant_id=l.tenant_id
        where l.purchase_order_id=:order_id and l.tenant_id=:tenant
        order by p.sku, p.name
    """), {"order_id": str(order_id), "tenant": str(tenant_id)}).mappings().all()
    payload = {
        "schema": "enterprisecloud.approval-document.v1",
        "document_type": "purchase_order",
        "number": header["number"],
        "supplier": {"code": header["supplier_code"], "name": header["supplier_name"], "tax_id": header["supplier_tax_id"]},
        "warehouse": {"code": header["warehouse_code"], "name": header["warehouse_name"]},
        "total": header["total"],
        "lines": [dict(line) for line in lines],
    }
    serialized = canonicalize(payload)
    digest = sha256(serialized.encode("utf-8")).hexdigest()
    return db.execute(text("""
        insert into approval_documents(
          tenant_id, approval_request_id, module, entity_type, entity_id,
          document_number, canonical_payload, content_hash, requested_by
        ) values (
          :tenant, :request, 'purchases', 'purchase_order', :order_id,
          :number, cast(:payload as jsonb), :digest, :requested_by
        ) returning *
    """), {
        "tenant": str(tenant_id), "request": str(approval_request_id), "order_id": str(order_id),
        "number": f"OC-{header['number']}", "payload": serialized, "digest": digest,
        "requested_by": str(requested_by),
    }).mappings().one()


def append_internal_decision_evidence(
    db: Session,
    *,
    tenant_id: UUID,
    approval_request_id: UUID,
    signer_user_id: UUID,
    decision: str,
) -> None:
    """Append the internal approval evidence; never mutate the original payload."""
    document = db.execute(text("""
        select id, content_hash from approval_documents
        where tenant_id=:tenant and approval_request_id=:request
        for update
    """), {"tenant": str(tenant_id), "request": str(approval_request_id)}).mappings().first()
    if not document:
        return
    evidence = {
        "approval_document_id": document["id"],
        "document_hash": document["content_hash"],
        "decision": decision,
        "signer_user_id": signer_user_id,
        "signature_type": "internal_approval",
    }
    digest = payload_hash(evidence)
    db.execute(text("""
        insert into approval_signatures(
          tenant_id, approval_document_id, approval_request_id, signer_user_id,
          signature_type, decision, signed_hash
        ) values (
          :tenant, :document, :request, :signer, 'internal_approval', :decision, :digest
        ) on conflict (approval_request_id) where signature_type='internal_approval' do nothing
    """), {
        "tenant": str(tenant_id), "document": str(document["id"]), "request": str(approval_request_id),
        "signer": str(signer_user_id), "decision": decision, "digest": digest,
    })
    db.execute(text("""
        update approval_documents set status=:decision, updated_at=now()
        where id=:document and tenant_id=:tenant and status='submitted'
    """), {"decision": decision, "document": str(document["id"]), "tenant": str(tenant_id)})
