"""Regression checks for the controlled commercial-return boundary."""

from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.transactions import CommercialReturnCreate, PurchaseCreate


def test_return_requires_a_meaningful_reason():
    with pytest.raises(ValidationError):
        CommercialReturnCreate(reason="no")


def test_purchase_can_be_explicitly_linked_to_its_order():
    payload = PurchaseCreate(
        purchase_order_id=uuid4(), supplier_id=uuid4(), warehouse_id=uuid4(),
        lines=[{"product_id": uuid4(), "quantity": 1, "unit_cost": "10.00"}],
    )
    assert payload.purchase_order_id is not None


def test_returns_preserve_tenant_and_fiscal_guardrails():
    sales = Path("app/api/routes/sales.py").read_text(encoding="utf-8")
    purchases = Path("app/api/routes/purchases.py").read_text(encoding="utf-8")

    assert "where tenant_id=:tenant and sale_id=:sale" in sales
    assert "CPE activo" in sales
    assert "return_tracked_sale_stock" in sales
    assert "where tenant_id=:tenant and purchase_id=:purchase" in purchases
    assert "reverse_tracked_purchase_receipt" in purchases
