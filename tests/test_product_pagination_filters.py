import inspect

from app.api.routes.products import list_products


def test_product_list_supports_server_side_search_and_active_filter():
    source = inspect.getsource(list_products)
    assert "Product.name.ilike" in source
    assert "Product.sku.ilike" in source
    assert "Product.is_active.is_(active)" in source
