from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes.imports import TEMPLATES, _ensure_import_module_allowed, _validate_rows
from app.security.auth import Actor


def test_inventory_template_includes_the_required_adjustment_number():
    """The downloadable header must match the server-side validation contract."""
    assert "number" in TEMPLATES["inventory"]["columns"]


def test_inventory_import_accepts_negative_whole_stock_adjustments():
    valid, errors = _validate_rows(
        "inventory",
        [{
            "number": "AJ-001",
            "sku": "SKU-001",
            "warehouse_code": "ALM-001",
            "quantity": "-4",
            "reason": "Inventario físico",
            "reference": "CONTEO-001",
            "note": "Diferencia verificada",
        }],
    )

    assert errors == []
    assert valid[0]["quantity"] == -4


def test_inventory_import_rejects_decimal_units_and_zero_adjustments():
    _, decimal_errors = _validate_rows(
        "inventory",
        [{"number": "AJ-001", "sku": "SKU-001", "warehouse_code": "ALM-001", "quantity": "1.5", "reason": "Conteo", "reference": "X", "note": ""}],
    )
    _, zero_errors = _validate_rows(
        "inventory",
        [{"number": "AJ-002", "sku": "SKU-001", "warehouse_code": "ALM-001", "quantity": "0", "reason": "Conteo", "reference": "X", "note": ""}],
    )

    assert decimal_errors[0]["message"] == "quantity debe ser un número entero"
    assert zero_errors[0]["message"] == "quantity no puede ser cero"


def test_retail_tenant_cannot_import_administrative_sales():
    actor = Actor(
        user_id="00000000-0000-0000-0000-000000000001",
        tenant_id="00000000-0000-0000-0000-000000000002",
        role_code="owner",
    )
    db = SimpleNamespace(get=lambda _model, _id: SimpleNamespace(business_profile="retail"))

    with pytest.raises(HTTPException, match="Punto de venta") as error:
        _ensure_import_module_allowed("sales", actor, db)

    assert error.value.status_code == 403
