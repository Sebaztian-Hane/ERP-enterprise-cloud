from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.entities import MembershipModuleSetting, Tenant, TenantMembership, TenantModuleSetting
from app.security.auth import Actor, ROLE_PERMISSIONS
from app.services.workspace_access import workspace_access


@pytest.fixture
def access_database():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[model.__table__ for model in (Tenant, TenantMembership, TenantModuleSetting, MembershipModuleSetting)])
    with Session(engine) as db:
        yield db
    engine.dispose()


def member(db, role="viewer", profile="hybrid"):
    tenant = Tenant(id=uuid4(), name="Empresa de pruebas", business_profile=profile)
    actor = Actor(user_id=uuid4(), tenant_id=tenant.id, role_code=role)
    membership = TenantMembership(id=uuid4(), tenant_id=tenant.id, user_id=actor.user_id, role_code=role)
    db.add(tenant); db.flush(); db.add(membership); db.commit()
    return tenant, actor, membership


@pytest.mark.parametrize("role", list(ROLE_PERMISSIONS))
def test_snapshot_returns_exact_existing_permissions_without_new_grants(access_database, role):
    _, actor, _ = member(access_database, role)
    snapshot = workspace_access(actor, access_database)
    assert set(snapshot["permissions"]) == ROLE_PERMISSIONS[role]
    assert snapshot["role_code"] == role


def test_member_override_cannot_enable_company_disabled_module(access_database):
    _, actor, membership = member(access_database, "owner")
    access_database.add(TenantModuleSetting(tenant_id=actor.tenant_id, module_code="inventory", is_enabled=False))
    access_database.add(MembershipModuleSetting(tenant_id=actor.tenant_id, membership_id=membership.id, module_code="inventory", is_enabled=True))
    access_database.commit()
    assert workspace_access(actor, access_database)["modules"]["inventory"] is False


def test_user_restriction_cannot_leak_from_another_tenant(access_database):
    _, actor_a, _ = member(access_database)
    _, actor_b, member_b = member(access_database)
    access_database.add(MembershipModuleSetting(tenant_id=actor_b.tenant_id, membership_id=member_b.id, module_code="products", is_enabled=False))
    access_database.commit()
    assert workspace_access(actor_a, access_database)["modules"]["products"] is True
    assert workspace_access(actor_b, access_database)["modules"]["products"] is False


def test_business_profile_cannot_be_overridden_by_company_or_member(access_database):
    _, actor, membership = member(access_database, "owner", "retail")
    access_database.add(TenantModuleSetting(tenant_id=actor.tenant_id, module_code="sales_orders", is_enabled=True))
    access_database.add(MembershipModuleSetting(tenant_id=actor.tenant_id, membership_id=membership.id, module_code="sales_orders", is_enabled=True))
    access_database.commit()
    assert workspace_access(actor, access_database)["modules"]["sales_orders"] is False


def test_inactive_membership_does_not_receive_workspace_access(access_database):
    _, actor, membership = member(access_database)
    membership.is_active = False
    access_database.commit()
    with pytest.raises(HTTPException) as error:
        workspace_access(actor, access_database)
    assert error.value.status_code == 403
