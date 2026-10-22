import inspect
from uuid import UUID

from app.api.routes.products import _resolve_unit, create_product, update_product
from app.schemas.catalog import ProductCreate, ProductUpdate


def test_product_creation_requires_a_master_unit_id():
    unit_id = "00000000-0000-0000-0000-000000000010"
    product = ProductCreate.model_validate({
        "sku": "SKU-001",
        "name": "Producto con unidad",
        "unit_id": unit_id,
    })
    assert product.unit_id == UUID(unit_id)


def test_product_update_accepts_a_unit_id_instead_of_free_text_unit():
    update = ProductUpdate.model_validate({"unit_id": "00000000-0000-0000-0000-000000000010"})
    assert update.unit_id is not None
    assert "unit" not in update.model_dump(exclude_unset=True)


def test_product_unit_resolution_and_change_guard_are_tenant_scoped():
    resolver = inspect.getsource(_resolve_unit)
    create = inspect.getsource(create_product)
    update = inspect.getsource(update_product)

    assert "UnitOfMeasure.tenant_id == tenant_id" in resolver
    assert "UnitOfMeasure.is_active.is_(True)" in resolver
    assert "unit_code=unit.code" in create
    assert "unit_name=unit.name" in create
    assert "InventoryMovement" in update
    assert "No se puede cambiar la unidad" in update
