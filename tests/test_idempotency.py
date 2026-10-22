from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.services.idempotency import canonical_hash, claim


def test_canonical_hash_is_stable_for_key_order_and_decimals():
    first = {"number": "V-1", "amount": Decimal("10.00"), "lines": [{"sku": "A"}]}
    second = {"lines": [{"sku": "A"}], "amount": Decimal("10.00"), "number": "V-1"}

    assert canonical_hash(first) == canonical_hash(second)


def test_idempotency_key_rejects_a_short_value_without_accessing_database():
    with pytest.raises(HTTPException) as error:
        claim(
            object(),
            tenant_id=__import__("uuid").uuid4(),
            actor_user_id=__import__("uuid").uuid4(),
            operation="sales.create",
            key="short",
            payload={"number": "V-1"},
        )

    assert error.value.status_code == 422
