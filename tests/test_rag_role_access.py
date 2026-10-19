from app.services.rag_access import assistant_scope, denied_topic


def test_sales_cannot_query_stock_or_warehouses():
    assert denied_topic("¿Cuánto stock hay de Malla Soldada?", "sales") == "inventory"
    assert denied_topic("¿Qué hay en el almacén central?", "sales") == "warehouses"


def test_sales_can_query_catalogue_and_own_sales_data():
    assert denied_topic("¿Cuál es el precio de Malla Soldada?", "sales") is None
    assert denied_topic("Resume las ventas de esta semana", "sales") is None
    assert assistant_scope("sales").allowed_sources == (
        "erp_guide", "product_catalog", "customer", "sale",
    )


def test_inventory_cannot_query_sales_or_finance():
    assert denied_topic("¿Cuáles fueron las ventas de hoy?", "inventory") == "sales"
    assert denied_topic("Muéstrame las cuentas por cobrar", "inventory") == "finance"


def test_owner_has_full_assistant_scope():
    assert denied_topic("Dame el stock y las ventas de hoy", "owner") is None


def test_unknown_role_fails_closed():
    assert assistant_scope("unknown").allowed_sources == ()
    assert denied_topic("¿Cuál es el stock disponible?", "unknown") == "inventory"
