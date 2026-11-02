"""Exercise staging, actual business writes, rollback and access in isolation."""
import io
from pathlib import Path
from datetime import datetime
from uuid import UUID, uuid4
from zipfile import ZipFile, ZIP_DEFLATED

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.main import app
from app.db.base import Base
from app.db.session import get_db
from app.models.entities import (Tenant, TenantMembership, TenantModuleSetting, MembershipModuleSetting, LegalEntity, Branch, Category, UnitOfMeasure, Product, ProductMaterialProfile, Customer, Supplier, Warehouse, Inventory, InventoryMovement, AuditLog)
from app.models.enterprise import EnterpriseRecord, EnterpriseRecordEvent
from app.models.migration_projects import MigrationProject, MigrationObject
from app.security.auth import Actor, get_actor
from app.services import audit
from app.services.migration_catalog import DEFINITIONS
from app.services.migration_examples import example_rows
from app.services.migration_workbooks import csv_bytes
from app.services.enterprise_catalog import RESOURCE_BY_CODE


@compiles(JSONB, "sqlite")
def sqlite_jsonb(_type, _compiler, **_kwargs): return "JSON"


@pytest.fixture
def suite(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    def connect(connection, _):
        connection.isolation_level = None
        connection.execute("PRAGMA foreign_keys=ON")
    event.listen(engine, "connect", connect)
    event.listen(engine, "begin", lambda connection: connection.exec_driver_sql("BEGIN"))
    models = (Tenant, TenantMembership, TenantModuleSetting, MembershipModuleSetting, LegalEntity, Branch, Category, UnitOfMeasure, Product, ProductMaterialProfile, Customer, Supplier, Warehouse, Inventory, InventoryMovement, AuditLog, EnterpriseRecord, EnterpriseRecordEvent, MigrationProject, MigrationObject)
    Base.metadata.create_all(engine, tables=[model.__table__ for model in models])
    state = {"actor": Actor(user_id=uuid4(), tenant_id=uuid4(), role_code="owner", assurance_level="aal2")}
    other = uuid4()
    with Session(engine) as db:
        db.add_all([Tenant(id=state["actor"].tenant_id, name="Evaluación ñ á é í ó ú"), Tenant(id=other, name="Otra empresa")]); db.flush()
        member = TenantMembership(tenant_id=state["actor"].tenant_id, user_id=state["actor"].user_id, role_code="owner")
        db.add(member)
        db.execute(text("create table cost_centers(id varchar(32) primary key, tenant_id varchar(32), code text, name text, is_active boolean)"))
        db.commit()
        def database():
            try: yield db
            finally: db.rollback()
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_actor] = lambda: state["actor"]
        # No external integration; retain the actual audit writes to test rollback.
        monkeypatch.setattr(audit, "queue_sync_event", lambda *_args, **_kwargs: None)
        with TestClient(app) as client: yield client, db, state, other, member
        app.dependency_overrides.clear()
    engine.dispose()


def count(db, model): return db.scalar(select(func.count()).select_from(model))


def create(suite, groups=None):
    client = suite[0]
    catalog = client.get("/api/v1/migration-projects/catalog").json()
    response = client.post("/api/v1/migration-projects", json={"request_id": str(uuid4()), "name": "Evaluación de carga", "groups": groups or [group["code"] for group in catalog["groups"]]})
    assert response.status_code == 201, response.text
    return response.json()


def upload(suite, project, code, rows=None, expected=200):
    rows = rows if rows is not None else example_rows()[code]
    columns = DEFINITIONS[code]["columns"]
    data = csv_bytes(columns, [[row.get(key, "") for key in columns] for row in rows])
    response = suite[0].post(f"/api/v1/migration-projects/{project['id']}/objects/{code}/file", data={"version": project["version"]}, files={"file": (code + ".csv", data, "text/csv")})
    assert response.status_code == expected, response.text
    return response.json()


def simulate(suite, project):
    response = suite[0].post(f"/api/v1/migration-projects/{project['id']}/simulate", json={"version": project["version"]})
    assert response.status_code == 200, response.text
    return response.json()


def commit(suite, project, expected=200, **changes):
    body = {"version": project["version"], "digest": project["simulation"]["digest"], "confirmed": True, **changes}
    response = suite[0].post(f"/api/v1/migration-projects/{project['id']}/commit", json=body)
    assert response.status_code == expected, response.text
    return response.json()


def stage_all(suite, project):
    for obj in project["objects"]:
        if obj["code"] not in {"sales", "purchases"}: project = upload(suite, project, obj["code"])
    return project


def test_all_31_nonfinancial_objects_simulate_without_business_writes_then_commit_once(suite):
    _, db, _, _, _ = suite
    project = stage_all(suite, create(suite))
    assert len(project["objects"]) == 33
    audit_before = count(db, AuditLog)
    project = simulate(suite, project)
    assert project["simulation"]["can_commit"], project["simulation"]["objects"]
    for model in (Product, ProductMaterialProfile, EnterpriseRecord, EnterpriseRecordEvent, Inventory, InventoryMovement, Customer, Supplier): assert count(db, model) == 0
    assert count(db, AuditLog) == audit_before
    assert count(db, MigrationObject) == 31
    result = commit(suite, project)
    assert result["status"] == "migrated"
    assert count(db, Product) == 2
    assert count(db, EnterpriseRecord) == 24
    assert count(db, InventoryMovement) == 2
    assert sorted(float(x) for x in db.scalars(select(Inventory.quantity))) == [20, 100]
    for row in db.scalars(select(EnterpriseRecord)):
        assert row.status == RESOURCE_BY_CODE[row.resource_code].initial
        assert row.version == 1
    assert commit(suite, project)["receipt"] == result["receipt"]
    assert count(db, EnterpriseRecord) == 24
    assert count(db, InventoryMovement) == 2


def test_final_confirmation_mfa_and_version_required(suite):
    client, db, state, _, _ = suite
    project = simulate(suite, upload(suite, create(suite, ["base"]), "units"))
    commit(suite, project, 422, confirmed=False)
    commit(suite, project, 409, version=project["version"] - 1)
    commit(suite, project, 409, digest="0" * 64)
    actor = state["actor"]
    state["actor"] = Actor(user_id=actor.user_id, tenant_id=actor.tenant_id, role_code="owner", assurance_level="aal1")
    commit(suite, project, 403)
    assert count(db, UnitOfMeasure) == 0


def test_current_database_conflict_rolls_back_entire_group(suite):
    _, db, state, _, _ = suite
    project = simulate(suite, upload(suite, upload(suite, create(suite, ["base"]), "units"), "products"))
    assert project["simulation"]["can_commit"]
    unit = UnitOfMeasure(tenant_id=state["actor"].tenant_id, code="EXIST", name="Existente")
    db.add(unit); db.flush()
    db.add(Product(tenant_id=state["actor"].tenant_id, sku="DEMO-PT", name="Concurrente", unit_id=unit.id, unit_code=unit.code, unit_name=unit.name)); db.commit()
    commit(suite, project, 422)
    assert count(db, Product) == 1
    assert count(db, UnitOfMeasure) == 1  # DEMO-U rolled back, even after successful write.
    assert db.get(MigrationProject, UUID(project["id"])).status == "ready"


def test_file_change_invalidates_simulation_and_stale_upload_rejected(suite):
    client, db, _, _, _ = suite
    project = simulate(suite, upload(suite, create(suite, ["base"]), "units"))
    old = project
    project = upload(suite, project, "units", [{"code": "NEW", "name": "Unidad nueva"}])
    assert not project["simulation"].get("can_commit")
    assert not project["simulation"].get("digest")
    assert project["simulation"]["upload_logs"][0]["object"] == "units"
    commit(suite, old, 409)
    upload(suite, old, "units", [{"code": "STALE", "name": "Desfasada"}], 409)
    assert count(db, UnitOfMeasure) == 0


@pytest.mark.parametrize("scope", ["tenant", "member"])
def test_disabled_modules_hide_templates_and_block_existing_group(suite, scope):
    client, db, state, _, member = suite
    project = create(suite, ["hcm"])
    if scope == "tenant": db.add(TenantModuleSetting(tenant_id=state["actor"].tenant_id, module_code="hcm", is_enabled=False))
    else: db.add(MembershipModuleSetting(tenant_id=state["actor"].tenant_id, membership_id=member.id, module_code="hcm", is_enabled=False))
    db.commit()
    assert "hcm" not in [item["module"] for item in client.get("/api/v1/migration-projects/catalog").json()["objects"]]
    assert client.get(f"/api/v1/migration-projects/{project['id']}").status_code == 403
    assert client.get("/api/v1/migration-projects/objects/employees/template").status_code == 403
    assert client.get("/api/v1/migration-projects").json()["projects"] == []


def test_tenant_isolation_and_specialized_role_not_owner(suite):
    client, db, state, other, _ = suite
    project = create(suite)
    actor = state["actor"]
    state["actor"] = Actor(user_id=actor.user_id, tenant_id=other, role_code="owner", assurance_level="aal2")
    assert client.get(f"/api/v1/migration-projects/{project['id']}").status_code == 404
    state["actor"] = Actor(user_id=actor.user_id, tenant_id=actor.tenant_id, role_code="hr", assurance_level="aal2")
    assert client.get("/api/v1/migration-projects/catalog").status_code == 403


def test_example_pack_roundtrip_and_zip_atomic_rejection(suite):
    client, db, _, _, _ = suite
    project = create(suite, ["hcm"])
    data = client.get(f"/api/v1/migration-projects/{project['id']}/example-pack")
    assert data.status_code == 200
    response = client.post(f"/api/v1/migration-projects/{project['id']}/upload-pack", data={"version": project["version"]}, files={"file": ("ejemplos.zip", data.content, "application/zip")})
    assert response.status_code == 200, response.text
    project = simulate(suite, response.json())
    assert project["simulation"]["can_commit"], project
    bad = io.BytesIO()
    with ZipFile(bad, "w", ZIP_DEFLATED) as archive:
        archive.writestr("employees.csv", csv_bytes(DEFINITIONS["employees"]["columns"], [["NEW", "Nombre", "Area", "Puesto", "2026-01-01", "", "indefinite", ""]]))
        archive.writestr("../attendance.csv", b"bad")
    before = project["version"]
    response = client.post(f"/api/v1/migration-projects/{project['id']}/upload-pack", data={"version": before}, files={"file": ("bad.zip", bad.getvalue(), "application/zip")})
    assert response.status_code == 422
    assert db.get(MigrationProject, UUID(project["id"])).version == before
    assert count(db, EnterpriseRecord) == 0


def test_row_errors_and_missing_references_prevent_commit(suite):
    project = create(suite, ["pp"])
    project = upload(suite, project, "bills_of_material")
    project = simulate(suite, project)
    assert not project["simulation"]["can_commit"]
    assert project["simulation"]["objects"]["bills_of_material"]["errors"]
    commit(suite, project, 409)


def test_optional_reference_does_not_require_unassigned_module(suite):
    client, db, state, _, _ = suite
    db.add(TenantModuleSetting(tenant_id=state["actor"].tenant_id, module_code="customers", is_enabled=False)); db.commit()
    catalog = client.get("/api/v1/migration-projects/catalog").json()
    opportunity = next(item for item in catalog["objects"] if item["code"] == "opportunities")
    assert opportunity["blocked_dependencies"] == []
    project = simulate(suite, upload(suite, create(suite, ["crm"]), "opportunities"))
    assert project["simulation"]["can_commit"], project["simulation"]


def test_distributed_excel_examples_preserve_dates_codes_and_all_dependencies(suite):
    client, db, _, _, _ = suite
    project = create(suite)
    source = Path(__file__).parents[1] / "examples/migrations/ejemplos-migracion-DUNSDEMO.zip"
    prepared = io.BytesIO()
    with ZipFile(source) as original, ZipFile(prepared, "w", ZIP_DEFLATED) as pack:
        assert len([name for name in original.namelist() if name.endswith(".xlsx")]) == 33
        for name in original.namelist():
            # Financial SQL is exercised by the PostgreSQL rollback smoke test.
            if name.endswith(".xlsx") and name not in {"sales.xlsx", "purchases.xlsx"}: pack.writestr(name, original.read(name))
    response = client.post(f"/api/v1/migration-projects/{project['id']}/upload-pack", data={"version": project["version"]}, files={"file": ("ejemplos.zip", prepared.getvalue(), "application/zip")})
    assert response.status_code == 200, response.text
    project = simulate(suite, response.json())
    assert project["simulation"]["can_commit"], project["simulation"]
    assert project["simulation"]["total_rows"] == 33
    assert count(db, EnterpriseRecord) == 0
    assert count(db, Inventory) == 0
