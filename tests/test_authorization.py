from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.api.routes.customers import list_customers
from app.api.routes.tenants import ensure_active_owner_remains, require_owner_role_change
from app.schemas.common import MembershipUpdate
from app.security import auth
from app.security.auth import Actor, ensure_token_not_revoked, get_actor, require_permission, require_second_factor, token_issued_at


class ScalarResult:
    def __init__(self, items):
        self.items = items

    def all(self):
        return self.items


class CaptureSession:
    def __init__(self, membership=None, items=None):
        self.membership = membership
        self.items = items or []
        self.statements = []

    def scalar(self, statement):
        self.statements.append(statement)
        return self.membership

    def scalars(self, statement):
        self.statements.append(statement)
        return ScalarResult(self.items)


def test_actor_requires_an_active_membership(monkeypatch):
    user_id, tenant_id = uuid4(), uuid4()
    monkeypatch.setattr(auth, "decode_access_token", lambda _: {"sub": str(user_id)})
    with pytest.raises(HTTPException) as error:
        get_actor("Bearer valid-token", tenant_id, CaptureSession())
    assert error.value.status_code == 403


def test_actor_uses_membership_of_requested_tenant(monkeypatch):
    user_id, tenant_id = uuid4(), uuid4()
    membership = SimpleNamespace(role_code="sales")
    db = CaptureSession(membership=membership)
    monkeypatch.setattr(auth, "decode_access_token", lambda _: {"sub": str(user_id)})
    actor = get_actor("Bearer valid-token", tenant_id, db)
    assert actor == Actor(user_id=user_id, tenant_id=tenant_id, role_code="sales")
    params = db.statements[0].compile(dialect=postgresql.dialect()).params
    assert tenant_id in params.values()
    assert user_id in params.values()


def test_permission_denies_role_without_write_access():
    check = require_permission("customers:write")
    with pytest.raises(HTTPException) as error:
        check(Actor(user_id=uuid4(), tenant_id=uuid4(), role_code="viewer"))
    assert error.value.status_code == 403


def test_sales_role_can_issue_its_own_electronic_documents():
    check = require_permission("electronic_billing:write")
    actor = Actor(user_id=uuid4(), tenant_id=uuid4(), role_code="sales")
    assert check(actor) == actor


def test_customer_listing_is_scoped_to_actor_tenant():
    tenant_id = uuid4()
    customer = SimpleNamespace(id=uuid4(), tenant_id=tenant_id, name="Empresa A")
    db = CaptureSession(items=[customer])
    result = list_customers(Actor(user_id=uuid4(), tenant_id=tenant_id, role_code="sales"), db)
    assert result == [customer]
    statement = db.statements[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert tenant_id in compiled.params.values()
    assert "customers.tenant_id" in str(compiled)


def test_only_owner_can_assign_owner_role():
    with pytest.raises(HTTPException) as error:
        require_owner_role_change(Actor(user_id=uuid4(), tenant_id=uuid4(), role_code="admin"), "owner")
    assert error.value.status_code == 403


def test_last_active_owner_cannot_be_suspended():
    membership = SimpleNamespace(id=uuid4(), tenant_id=uuid4(), role_code="owner", is_active=True)
    db = SimpleNamespace(scalar=lambda _: 1)
    with pytest.raises(HTTPException) as error:
        ensure_active_owner_remains(db, membership, MembershipUpdate(is_active=False))
    assert error.value.status_code == 422


def test_token_revocation_watermark_rejects_old_access_token():
    user_id = uuid4()
    state = SimpleNamespace(valid_after_iat=1_000)
    db = SimpleNamespace(get=lambda model, value: state if value == user_id else None)
    with pytest.raises(HTTPException) as error:
        ensure_token_not_revoked(db, user_id, {"iat": 1_000})
    assert error.value.status_code == 401
    ensure_token_not_revoked(db, user_id, {"iat": 1_001})


def test_token_issued_at_rejects_missing_or_boolean_claims():
    for claims in ({}, {"iat": True}):
        with pytest.raises(HTTPException) as error:
            token_issued_at(claims)
        assert error.value.status_code == 401


def test_high_risk_action_requires_aal2_session():
    aal1_actor = Actor(user_id=uuid4(), tenant_id=uuid4(), role_code="owner")
    with pytest.raises(HTTPException) as error:
        require_second_factor(aal1_actor)
    assert error.value.status_code == 403
    aal2_actor = Actor(user_id=uuid4(), tenant_id=uuid4(), role_code="owner", assurance_level="aal2")
    assert require_second_factor(aal2_actor) == aal2_actor
