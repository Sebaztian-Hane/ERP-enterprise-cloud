"""Regression checks for writes that complete multi-step tenant workflows."""

from pathlib import Path


def _source(name: str) -> str:
    return Path("app/api/routes", name).read_text(encoding="utf-8")


def test_inventory_count_application_and_signature_consumption_are_tenant_scoped():
    source = _source("operations.py")

    assert "where id=:id and tenant_id=:tenant and status in ('submitted', 'approved')" in source
    assert "where id=:id and tenant_id=:tenant and used_at is null" in source


def test_workflow_conversion_writes_and_duplicate_checks_are_tenant_scoped():
    source = _source("workflow.py")

    assert "where id=:quote and tenant_id=:tenant and sales_order_id is null" in source
    assert "sales_order_id=:id and tenant_id=:tenant" in source
    assert "where id=:sale and tenant_id=:tenant and sales_order_id is null" in source
    assert "purchase_order_id=:id and tenant_id=:tenant" in source
    assert "where id=:purchase and tenant_id=:tenant and purchase_order_id is null" in source
