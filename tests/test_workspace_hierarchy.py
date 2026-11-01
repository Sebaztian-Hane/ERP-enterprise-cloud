from uuid import uuid4

import pytest
from sqlalchemy import select

from test_migration_projects import suite
from app.models.entities import Customer, MembershipModuleSetting, TenantMembership, TenantModuleSetting
from app.security.auth import Actor
from app.services.workspace_access import workspace_access


@pytest.mark.parametrize("role", ["admin", "sales", "inventory", "finance", "hr", "viewer"])
def test_governance_endpoints_are_owner_only(suite, role):
    client, _, state, _, member = suite
    actor = state["actor"]
    state["actor"] = Actor(user_id=actor.user_id, tenant_id=actor.tenant_id, role_code=role, assurance_level="aal2")
    for path in ["/tenants/memberships", "/tenants/roles", "/tenants/module-access", "/tenants/audit-logs", "/migration-projects/catalog", "/imports/templates", "/operations/configuration/exchange-rates"]:
        response = client.get("/api/v1" + path)
        assert response.status_code == 403, (path, response.text)
    response = client.patch(f"/api/v1/tenants/memberships/{member.id}/module-access", json={"modules": {"sales": True}})
    assert response.status_code == 403


@pytest.mark.parametrize("role,path", [("sales", "/enterprise/opportunities/records"), ("sales", "/enterprise/service_tickets/records"), ("inventory", "/enterprise/equipment/records"), ("inventory", "/enterprise/trips/records"), ("finance", "/enterprise/fixed_assets/records"), ("finance", "/sales")])
def test_job_roles_cannot_open_another_specialty_through_the_api(suite, role, path):
    client, _, state, _, _ = suite
    actor = state["actor"]
    state["actor"] = Actor(user_id=actor.user_id, tenant_id=actor.tenant_id, role_code=role)
    assert client.get("/api/v1" + path).status_code == 403


@pytest.mark.parametrize("scope", ["company", "member"])
def test_disabled_sd_parent_blocks_child_api_and_access_snapshot(suite, scope):
    client, db, state, _, member = suite
    actor = state["actor"]
    setting = TenantModuleSetting(tenant_id=actor.tenant_id, module_code="sales", is_enabled=False) if scope == "company" else MembershipModuleSetting(tenant_id=actor.tenant_id, membership_id=member.id, module_code="sales", is_enabled=False)
    db.add(setting); db.commit()
    for code in ["sales", "customers", "quotes", "sales_orders", "pos"]:
        assert workspace_access(actor, db)["modules"][code] is False
    assert client.get("/api/v1/customers").status_code == 403
    assert client.get("/api/v1/quotes").status_code == 403


def test_owner_assigns_restrictions_atomically_without_granting_another_role(suite):
    client, db, state, other, _ = suite
    actor = state["actor"]
    member = TenantMembership(id=uuid4(), tenant_id=actor.tenant_id, user_id=uuid4(), role_code="sales")
    foreign = TenantMembership(id=uuid4(), tenant_id=other, user_id=uuid4(), role_code="sales")
    db.add_all([member, foreign]); db.commit()
    member_id, foreign_id = member.id, foreign.id
    response = client.patch(f"/api/v1/tenants/memberships/{member_id}/module-access", json={"modules": {"quotes": False, "maintenance": True}})
    assert response.status_code == 200, response.text
    assert client.patch(f"/api/v1/tenants/memberships/{foreign_id}/module-access", json={"modules": {"sales": False}}).status_code == 404
    assert client.patch(f"/api/v1/tenants/memberships/{member_id}/module-access", json={"modules": {"sales": False, "unknown": True}}).status_code == 422
    assert db.scalar(select(MembershipModuleSetting).where(MembershipModuleSetting.membership_id == member_id, MembershipModuleSetting.module_code == "sales")) is None
    state["actor"] = Actor(user_id=member.user_id, tenant_id=actor.tenant_id, role_code="sales")
    assert client.get("/api/v1/quotes").status_code == 403
    assert client.get("/api/v1/enterprise/equipment/records").status_code == 403


def test_owner_module_assignment_requires_second_factor_and_keeps_governance(suite):
    client, _, state, _, member = suite
    actor = state["actor"]
    assert client.patch(f"/api/v1/tenants/memberships/{member.id}/module-access", json={"modules": {"administration": False}}).status_code == 422
    state["actor"] = Actor(user_id=actor.user_id, tenant_id=actor.tenant_id, role_code="owner", assurance_level="aal1")
    assert client.patch(f"/api/v1/tenants/memberships/{member.id}/module-access", json={"modules": {"sales": False}}).status_code == 403


def test_fi_counterpart_lookup_returns_only_tenant_identity_and_display_name(suite):
    client, db, state, other, _ = suite
    actor = state["actor"]
    customer = Customer(id=uuid4(), tenant_id=actor.tenant_id, code="A", name="Cliente autorizado", tax_id="12345678", email="private@example.test")
    db.add_all([customer, Customer(tenant_id=other, code="B", name="Otra empresa")]); db.commit()
    identifier = str(customer.id)
    state["actor"] = Actor(user_id=actor.user_id, tenant_id=actor.tenant_id, role_code="finance")
    response = client.get("/api/v1/accounts/counterparts/customers")
    assert response.status_code == 200, response.text
    assert response.json() == [{"id": identifier, "name": "Cliente autorizado"}]
    assert client.get("/api/v1/customers").status_code == 403


def test_disabled_fi_also_blocks_its_shared_documents_and_reports(suite):
    client, db, state, _, member = suite
    actor = state["actor"]
    db.add(MembershipModuleSetting(tenant_id=actor.tenant_id, membership_id=member.id, module_code="finance", is_enabled=False))
    db.commit()
    state["actor"] = Actor(user_id=actor.user_id, tenant_id=actor.tenant_id, role_code="finance")
    for path in ["/accounts/counterparts/customers", "/documents", "/reports/dashboard"]:
        assert client.get("/api/v1" + path).status_code == 403


def test_provider_batch_validates_every_change_before_persisting(suite):
    from app.main import app
    from app.models.entities import Tenant
    from app.security.auth import PlatformActor, require_platform_admin

    client, db, state, other, member = suite
    actor = state["actor"]
    app.dependency_overrides[require_platform_admin] = lambda: PlatformActor(user_id=actor.user_id, email="provider@example.test")
    root = f"/api/v1/platform/tenants/{actor.tenant_id}"
    assert client.patch(root + "/modules/batch", json={"modules": {"sales": False, "unknown": True}}).status_code == 422
    assert db.get(TenantModuleSetting, {"tenant_id": actor.tenant_id, "module_code": "sales"}) is None
    response = client.patch(root + "/modules/batch", json={"modules": {"sales": False, "quotes": False}})
    assert response.status_code == 200, response.text
    assert not db.get(TenantModuleSetting, {"tenant_id": actor.tenant_id, "module_code": "sales"}).is_enabled
    assert client.patch(f"/api/v1/platform/tenants/{other}/members/{member.id}/modules/batch", json={"modules": {"quotes": False}}).status_code == 404
    assert client.patch(root + f"/members/{member.id}/modules/batch", json={"modules": {"administration": False}}).status_code == 422
    response = client.patch(root + f"/members/{member.id}/modules/batch", json={"modules": {"quotes": False, "customers": False}})
    assert response.status_code == 200, response.text
    for code in ["quotes", "customers"]:
        assert not db.scalar(select(MembershipModuleSetting).where(MembershipModuleSetting.membership_id == member.id, MembershipModuleSetting.module_code == code)).is_enabled
    tenant = db.get(Tenant, actor.tenant_id)
    tenant.business_profile = "wholesale"
    db.commit()
    assert client.patch(root + "/modules/batch", json={"modules": {"sales": True, "pos": True}}).status_code == 422
    assert not db.get(TenantModuleSetting, {"tenant_id": actor.tenant_id, "module_code": "sales"}).is_enabled
