from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.routes.inventory import _ensure_branch_in_tenant
from app.schemas.inventory import WarehouseCreate, WarehouseUpdate
from app.security.auth import Actor


class ScalarDatabase:
    def __init__(self, result):
        self.result = result

    def scalar(self, _statement):
        return self.result


def test_warehouse_schema_accepts_an_optional_branch_assignment():
    branch_id = uuid4()
    create = WarehouseCreate(code="ALM-01", name="Principal", branch_id=branch_id)
    update = WarehouseUpdate(branch_id=None)

    assert create.branch_id == branch_id
    assert update.branch_id is None


def test_warehouse_rejects_branch_outside_active_tenant():
    actor = Actor(user_id=uuid4(), tenant_id=uuid4(), role_code="inventory")

    with pytest.raises(HTTPException) as exc:
        _ensure_branch_in_tenant(uuid4(), actor, ScalarDatabase(None))

    assert exc.value.status_code == 404
    assert "no pertenece" in exc.value.detail


def test_warehouse_allows_verified_branch_from_same_tenant():
    actor = Actor(user_id=uuid4(), tenant_id=uuid4(), role_code="inventory")
    _ensure_branch_in_tenant(uuid4(), actor, ScalarDatabase(uuid4()))
