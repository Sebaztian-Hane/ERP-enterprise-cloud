import inspect

from app.api.routes.purchases import receive_purchase
from app.api.routes.sales import post_sale
from app.schemas.transactions import PurchaseCreate, SaleCreate
from app.services.payment_terms import resolve_payment_term


def test_sale_and_purchase_accept_payment_term_context():
    term_id = "00000000-0000-0000-0000-000000000003"
    sale = SaleCreate.model_validate({
        "customer_id": "00000000-0000-0000-0000-000000000001",
        "warehouse_id": "00000000-0000-0000-0000-000000000002",
        "payment_term_id": term_id,
        "lines": [{"product_id": "00000000-0000-0000-0000-000000000004", "quantity": 1, "unit_price": 10}],
    })
    purchase = PurchaseCreate.model_validate({
        "supplier_id": "00000000-0000-0000-0000-000000000005",
        "warehouse_id": "00000000-0000-0000-0000-000000000002",
        "payment_term_id": term_id,
        "lines": [{"product_id": "00000000-0000-0000-0000-000000000004", "quantity": 1, "unit_cost": 10}],
    })
    assert sale.payment_term_id is not None
    assert purchase.payment_term_id is not None


def test_posting_uses_server_resolved_payment_term_and_due_date():
    sale_source = inspect.getsource(post_sale)
    purchase_source = inspect.getsource(receive_purchase)
    resolver_source = inspect.getsource(resolve_payment_term)

    assert "resolve_payment_term(" in sale_source
    assert "resolve_payment_term(" in purchase_source
    assert "due_date=due_date" in sale_source
    assert "due_date=due_date" in purchase_source
    assert "tenant_id=:tenant" in resolver_source
    assert "supplied_due_date" in resolver_source
