from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.entities import Branch, LegalEntity, Warehouse
from app.services.operational_context import resolve_warehouse_context


class ScalarSequenceDatabase:
    def __init__(self, *results):
        self._results = list(results)

    def scalar(self, _statement):
        return self._results.pop(0)


def test_legacy_tenant_can_use_active_warehouse_without_legal_setup():
    tenant_id = uuid4()
    warehouse = Warehouse(id=uuid4(), tenant_id=tenant_id, code="ALM", name="Principal", is_active=True)

    context = resolve_warehouse_context(
        ScalarSequenceDatabase(warehouse, None), tenant_id=tenant_id, warehouse_id=warehouse.id,
    )

    assert context.warehouse is warehouse
    assert context.branch is None
    assert context.legal_entity is None


def test_configured_tenant_requires_warehouse_branch_assignment():
    tenant_id = uuid4()
    warehouse = Warehouse(id=uuid4(), tenant_id=tenant_id, code="ALM", name="Principal", is_active=True)

    with pytest.raises(HTTPException) as exc:
        resolve_warehouse_context(
            ScalarSequenceDatabase(warehouse, uuid4()), tenant_id=tenant_id, warehouse_id=warehouse.id,
        )

    assert exc.value.status_code == 422
    assert "sucursal activa" in exc.value.detail


def test_context_uses_only_active_branch_and_entity_of_same_tenant():
    tenant_id = uuid4()
    entity = LegalEntity(
        id=uuid4(), tenant_id=tenant_id, code="EC", legal_name="Empresa SAC",
        tax_id="20123456789", fiscal_address="Av. Lima 123", is_active=True,
    )
    branch = Branch(
        id=uuid4(), tenant_id=tenant_id, legal_entity_id=entity.id,
        code="001", name="Principal", is_active=True,
    )
    warehouse = Warehouse(
        id=uuid4(), tenant_id=tenant_id, code="ALM", name="Principal",
        branch_id=branch.id, is_active=True,
    )

    context = resolve_warehouse_context(
        ScalarSequenceDatabase(warehouse, branch, entity), tenant_id=tenant_id, warehouse_id=warehouse.id,
    )

    assert context.branch is branch
    assert context.legal_entity is entity
