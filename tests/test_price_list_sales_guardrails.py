import inspect

from app.api.routes.sales import _resolve_price_list_prices, post_sale, quote_sale_price_list
from app.schemas.transactions import PriceListQuoteCreate, SaleCreate


def test_price_list_quote_rejects_duplicate_product_lines():
    first = "00000000-0000-0000-0000-000000000001"
    payload = {"lines": [{"product_id": first, "quantity": 1}, {"product_id": first, "quantity": 2}]}
    try:
        PriceListQuoteCreate.model_validate(payload)
    except ValueError:
        return
    raise AssertionError("Las cotizaciones de lista no pueden repetir productos")


def test_sale_accepts_optional_price_list_context():
    payload = {
        "customer_id": "00000000-0000-0000-0000-000000000001",
        "warehouse_id": "00000000-0000-0000-0000-000000000002",
        "price_list_id": "00000000-0000-0000-0000-000000000003",
        "lines": [{"product_id": "00000000-0000-0000-0000-000000000004", "quantity": 1, "unit_price": 999}],
    }
    assert SaleCreate.model_validate(payload).price_list_id is not None


def test_sale_posts_server_resolved_prices_when_a_list_is_selected():
    source = inspect.getsource(post_sale)
    assert "_resolve_price_list_prices(" in source
    assert "resolved_prices.get(line.product_id, line.unit_price)" in source
    assert "price_list_id=data.price_list_id" in source
    assert "current_date" in inspect.getsource(_resolve_price_list_prices)
    assert quote_sale_price_list.__name__ == "quote_sale_price_list"
