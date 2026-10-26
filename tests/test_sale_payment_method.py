from uuid import UUID

import pytest
from pydantic import ValidationError

from app.schemas.transactions import SaleCreate


def sale_payload() -> dict:
    return {
        "customer_id": "00000000-0000-0000-0000-000000000001",
        "warehouse_id": "00000000-0000-0000-0000-000000000002",
        "lines": [{"product_id": "00000000-0000-0000-0000-000000000003", "quantity": 1, "unit_price": 10}],
    }


def test_sale_accepts_a_supported_b2b_payment_method():
    sale = SaleCreate.model_validate({**sale_payload(), "payment_method": "bank_transfer"})

    assert sale.payment_method == "bank_transfer"


def test_sale_rejects_an_unknown_payment_method():
    with pytest.raises(ValidationError, match="payment_method"):
        SaleCreate.model_validate({**sale_payload(), "payment_method": "crypto"})
