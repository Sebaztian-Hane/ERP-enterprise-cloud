from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.routes.sales import _validate_pos_tender_total
from app.schemas.transactions import PosCheckoutCreate, PosTenderCreate


def _payload(**overrides):
    payload = {
        "warehouse_id": uuid4(),
        "document_type": "ticket",
        "lines": [{"product_id": uuid4(), "quantity": "2"}],
        "tenders": [{"method": "cash", "amount": "20.00"}],
    }
    payload.update(overrides)
    return payload


def test_pos_checkout_contract_accepts_consumer_final_ticket():
    checkout = PosCheckoutCreate(**_payload())

    assert checkout.customer_id is None
    assert checkout.document_type == "ticket"
    assert checkout.lines[0].quantity == Decimal("2")


def test_pos_checkout_contract_rejects_duplicate_products():
    product_id = uuid4()
    with pytest.raises(ValidationError, match="Un producto"):
        PosCheckoutCreate(**_payload(lines=[
            {"product_id": product_id, "quantity": "1"},
            {"product_id": product_id, "quantity": "1"},
        ]))


def test_pos_checkout_persists_an_internal_ticket_for_every_sale():
    migration = Path("alembic/versions/0062_pos_receipts.sql").read_text(encoding="utf-8")

    assert "create table public.pos_receipts" in migration.lower()
    assert "unique(tenant_id, sale_id)" in migration.lower()
    assert "ticket_number" in migration.lower()


def test_pos_tenders_must_cover_the_server_calculated_total_exactly():
    with pytest.raises(Exception, match="coincidir"):
        _validate_pos_tender_total(
            total=Decimal("20.00"),
            tenders=[PosTenderCreate(method="cash", amount=Decimal("19.99"))],
        )
