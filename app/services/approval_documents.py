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


def create_petty_cash_evidence(
    db: Session,
    *,
    tenant_id: UUID,
    entity_type: str,
    entity_id: UUID,
    approval_request_id: UUID,
    requested_by: UUID,
) -> dict[str, Any]:
    """Persist a deterministic rendering of a petty-cash request for approval."""
    if entity_type == "petty_cash_expense":
        row = db.execute(text("""
            select e.number, e.expense_date, e.description, e.merchant_name,
                   e.supplier_tax_id, e.receipt_number, e.amount, e.expense_account_code,
                   f.code as fund_code, f.name as fund_name, f.custodian_user_id
            from petty_cash_expenses e
            join petty_cash_funds f on f.id=e.fund_id and f.tenant_id=e.tenant_id
            where e.id=:id and e.tenant_id=:tenant
        """), {"id": str(entity_id), "tenant": str(tenant_id)}).mappings().one()
        payload = {
            "schema": "enterprisecloud.approval-document.v1", "document_type": entity_type,
            "number": row["number"], "expense_date": row["expense_date"],
            "fund": {"code": row["fund_code"], "name": row["fund_name"], "custodian_user_id": row["custodian_user_id"]},
            "description": row["description"], "merchant_name": row["merchant_name"],
            "supplier_tax_id": row["supplier_tax_id"], "receipt_number": row["receipt_number"],
            "amount": row["amount"], "expense_account_code": row["expense_account_code"],
        }
    elif entity_type == "petty_cash_replenishment":
        row = db.execute(text("""
            select r.number, r.amount, f.code as fund_code, f.name as fund_name,
                   f.fixed_amount, f.current_balance, f.custodian_user_id
            from petty_cash_replenishments r
            join petty_cash_funds f on f.id=r.fund_id and f.tenant_id=r.tenant_id
            where r.id=:id and r.tenant_id=:tenant
        """), {"id": str(entity_id), "tenant": str(tenant_id)}).mappings().one()
        payload = {
            "schema": "enterprisecloud.approval-document.v1", "document_type": entity_type,
            "number": row["number"], "fund": {"code": row["fund_code"], "name": row["fund_name"], "custodian_user_id": row["custodian_user_id"]},
            "amount": row["amount"], "fixed_amount": row["fixed_amount"], "balance_before_replenishment": row["current_balance"],
        }
    else:
        raise ValueError("Tipo de documento de caja chica no soportado")
    serialized = canonicalize(payload)
    digest = sha256(serialized.encode("utf-8")).hexdigest()
    return db.execute(text("""
        insert into approval_documents(
          tenant_id, approval_request_id, module, entity_type, entity_id,
          document_number, canonical_payload, content_hash, requested_by
        ) values (
          :tenant, :request, 'treasury', :entity_type, :entity_id,
          :number, cast(:payload as jsonb), :digest, :requested_by
        ) returning *
    """), {
        "tenant": str(tenant_id), "request": str(approval_request_id),
        "entity_type": entity_type, "entity_id": str(entity_id),
        "number": f"CC-{row['number']}", "payload": serialized, "digest": digest,
        "requested_by": str(requested_by),
    }).mappings().one()


def create_payment_evidence(
    db: Session, *, tenant_id: UUID, payment_id: UUID, approval_request_id: UUID, requested_by: UUID,
) -> dict[str, Any]:
    row = db.execute(text("""
        select p.number,p.amount,p.method,p.reference,p.note,s.name as supplier_name,c.name as cash_account_name
        from payments p join suppliers s on s.id=p.supplier_id and s.tenant_id=p.tenant_id
        join cash_accounts c on c.id=p.cash_account_id and c.tenant_id=p.tenant_id
        where p.id=:id and p.tenant_id=:tenant and p.payment_type='payment'
    """), {"id": str(payment_id), "tenant": str(tenant_id)}).mappings().one()
    allocations = db.execute(text("""
        select a.amount, ap.purchase_id from payment_allocations a
        join accounts_payable ap on ap.id=a.payable_id and ap.tenant_id=a.tenant_id
        where a.payment_id=:payment and a.tenant_id=:tenant order by a.id
    """), {"payment": str(payment_id), "tenant": str(tenant_id)}).mappings().all()
    payload = {
        "schema": "enterprisecloud.approval-document.v1", "document_type": "supplier_payment",
        "number": row["number"], "supplier": row["supplier_name"], "amount": row["amount"],
        "method": row["method"], "reference": row["reference"], "note": row["note"],
        "cash_account": row["cash_account_name"],
        "allocations": [{"purchase_id": item["purchase_id"], "amount": item["amount"]} for item in allocations],
    }
    serialized = canonicalize(payload)
    digest = sha256(serialized.encode("utf-8")).hexdigest()
    return db.execute(text("""
        insert into approval_documents(tenant_id,approval_request_id,module,entity_type,entity_id,document_number,canonical_payload,content_hash,requested_by)
        values(:tenant,:request,'payments','supplier_payment',:payment,:number,cast(:payload as jsonb),:digest,:requested_by)
        returning *
    """), {"tenant": str(tenant_id), "request": str(approval_request_id), "payment": str(payment_id),
           "number": f"PAG-{row['number']}", "payload": serialized, "digest": digest, "requested_by": str(requested_by)}).mappings().one()


def create_inventory_adjustment_evidence(db: Session, *, tenant_id: UUID, adjustment_id: UUID, approval_request_id: UUID, requested_by: UUID) -> dict[str, Any]:
    row = db.execute(text("""select r.*,p.sku,p.name as product_name,w.code as warehouse_code,w.name as warehouse_name
      from inventory_adjustment_requests r join products p on p.id=r.product_id join warehouses w on w.id=r.warehouse_id
      where r.id=:id and r.tenant_id=:tenant"""), {"id":str(adjustment_id),"tenant":str(tenant_id)}).mappings().one()
    serialized = canonicalize({"schema":"enterprisecloud.approval-document.v1","document_type":"inventory_adjustment","reference":row["reference"],"product":{"sku":row["sku"],"name":row["product_name"]},"warehouse":{"code":row["warehouse_code"],"name":row["warehouse_name"]},"quantity":row["quantity"],"reason":row["reason"],"note":row["note"]})
    return db.execute(text("""insert into approval_documents(tenant_id,approval_request_id,module,entity_type,entity_id,document_number,canonical_payload,content_hash,requested_by)
      values(:tenant,:request,'inventory','inventory_adjustment',:id,:number,cast(:payload as jsonb),:digest,:actor) returning *"""), {"tenant":str(tenant_id),"request":str(approval_request_id),"id":str(adjustment_id),"number":f"AJ-{str(adjustment_id)[:8]}","payload":serialized,"digest":sha256(serialized.encode()).hexdigest(),"actor":str(requested_by)}).mappings().one()


def append_internal_decision_evidence(
    db: Session,
    *,
    tenant_id: UUID,
    approval_request_id: UUID,
    signer_user_id: UUID,
    decision: str,
    signed_hash: str | None = None,
    signing_key_id: UUID | None = None,
    signature_value: str | None = None,
    signature_challenge_id: UUID | None = None,
    delegated_from_user_id: UUID | None = None,
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
    digest = signed_hash or payload_hash(evidence)
    db.execute(text("""
        insert into approval_signatures(
          tenant_id, approval_document_id, approval_request_id, signer_user_id,
          signature_type, decision, signed_hash, signing_key_id, signature_algorithm, signature_value
          ,signature_challenge_id, delegated_from_user_id
        ) values (
          :tenant, :document, :request, :signer, 'internal_approval', :decision, :digest,
          :key, :algorithm, :signature, :challenge, :delegated_from
        ) on conflict (approval_request_id) where signature_type='internal_approval' do nothing
    """), {
        "tenant": str(tenant_id), "document": str(document["id"]), "request": str(approval_request_id),
        "signer": str(signer_user_id), "decision": decision, "digest": digest,
        "key": str(signing_key_id) if signing_key_id else None,
        "algorithm": "ECDSA_P256_SHA256" if signing_key_id else None,
        "signature": signature_value,
        "challenge": str(signature_challenge_id) if signature_challenge_id else None,
        "delegated_from": str(delegated_from_user_id) if delegated_from_user_id else None,
    })
    db.execute(text("""
        update approval_documents set status=:decision, updated_at=now()
        where id=:document and tenant_id=:tenant and status='submitted'
    """), {"decision": decision, "document": str(document["id"]), "tenant": str(tenant_id)})
