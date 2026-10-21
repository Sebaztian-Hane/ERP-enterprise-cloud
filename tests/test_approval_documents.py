from datetime import date
from decimal import Decimal

from app.services.approval_documents import canonicalize, payload_hash


def test_evidence_payload_hash_is_stable_across_key_order_and_supported_types():
    left = {"number": "INV-001", "amount": Decimal("45.00"), "count_date": date(2026, 10, 21)}
    right = {"count_date": date(2026, 10, 21), "amount": Decimal("45.00"), "number": "INV-001"}

    assert canonicalize(left) == canonicalize(right)
    assert payload_hash(left) == payload_hash(right)
    assert len(payload_hash(left)) == 64


def test_evidence_payload_hash_changes_when_business_content_changes():
    original = {"number": "INV-001", "lines": [{"sku": "A", "counted_quantity": 3}]}
    modified = {"number": "INV-001", "lines": [{"sku": "A", "counted_quantity": 4}]}

    assert payload_hash(original) != payload_hash(modified)
