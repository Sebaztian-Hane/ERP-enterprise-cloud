"""Exercise business lifecycles in an isolated relational store, never production."""

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.entities import Branch, Category, Customer, Product, Supplier, Tenant, UnitOfMeasure, Warehouse
from app.models.enterprise import EnterpriseRecord, EnterpriseRecordEvent
from app.security import auth
from app.security.auth import Actor, get_actor
from app.services import enterprise as service
from app.services.enterprise_catalog import RESOURCE_BY_CODE
from app.services.enterprise_roles import SPECIALIZED_ROLES


@compiles(JSONB, "sqlite")
def sqlite_jsonb(_type, _compiler, **_kwargs):
    return "JSON"


@pytest.fixture
def suite(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    models = (Tenant, Branch, Category, UnitOfMeasure, Customer, Supplier, Warehouse, Product, EnterpriseRecord, EnterpriseRecordEvent)
    Base.metadata.create_all(engine, tables=[item.__table__ for item in models])
    state = {"actor": Actor(user_id=uuid4(), tenant_id=uuid4(), role_code="owner", assurance_level="aal2")}
    other = uuid4()
    with Session(engine) as db:
        db.add_all([Tenant(id=state["actor"].tenant_id, name="DUNS - Evaluación ERP"), Tenant(id=other, name="Otra empresa")])
        db.flush()
        unit = UnitOfMeasure(tenant_id=state["actor"].tenant_id, code="NIU", name="Unidad")
        db.add(unit); db.flush()
        core = {}
        for name, model in (("customer", Customer), ("supplier", Supplier), ("warehouse", Warehouse)):
            row = model(tenant_id=state["actor"].tenant_id, code=name.upper(), name="Operación ñ á é í ó ú")
            db.add(row); db.flush(); core[name] = str(row.id)
        for name in ("product", "component"):
            row = Product(tenant_id=state["actor"].tenant_id, sku=name.upper(), name="Material", unit_id=unit.id, unit_code="NIU", unit_name="Unidad", average_cost=7)
            db.add(row); db.flush(); core[name] = str(row.id)
        # These existing core documents use SQL routes rather than ORM models.
        db.execute(text("create table cost_centers (id varchar(32), tenant_id varchar(32), code text, name text, is_active boolean)"))
        db.execute(text("create table purchase_orders (id varchar(32), tenant_id varchar(32), number text, status text)"))
        for table, code in (("cost_centers", "cost_center"), ("purchase_orders", "purchase_order")):
            identifier = uuid4(); core[code] = str(identifier)
            if table == "cost_centers":
                db.execute(text("insert into cost_centers values (:id,:tenant,'CC','Operación',true)"), {"id": identifier.hex, "tenant": state["actor"].tenant_id.hex})
            else:
                db.execute(text("insert into purchase_orders values (:id,:tenant,'OC-1','approved')"), {"id": identifier.hex, "tenant": state["actor"].tenant_id.hex})
        db.commit()

        def database():
            try: yield db
            finally: db.rollback()

        app.dependency_overrides[get_actor] = lambda: state["actor"]
        app.dependency_overrides[get_db] = database
        monkeypatch.setattr(auth, "module_is_enabled", lambda *_: True)
        monkeypatch.setattr(service, "module_is_enabled", lambda *_: True)
        monkeypatch.setattr(service, "audit", lambda *_args, **_kwargs: None)
        with TestClient(app) as client:
            yield client, db, state, other, core
        app.dependency_overrides.clear()
    engine.dispose()


def actor_as(state, *, user=None, role=None, assurance="aal2", tenant=None):
    current = state["actor"]
    state["actor"] = Actor(user_id=user or current.user_id, tenant_id=tenant or current.tenant_id, role_code=role or current.role_code, assurance_level=assurance)


def seed(suite, code, data, status=None):
    _, db, state, _, _ = suite
    definition = RESOURCE_BY_CODE[code]
    row = EnterpriseRecord(tenant_id=state["actor"].tenant_id, module_code=definition.module, resource_code=code, number=str(uuid4())[:12], title="Documento de origen", status=status or definition.initial, data=data, derived={}, version=1, created_by=uuid4(), updated_by=uuid4())
    db.add(row); db.commit()
    return str(row.id)


def sample(suite, code):
    _, _, _, _, core = suite
    employee = {"department": "Producción", "position": "Técnico", "hire_date": "2026-01-01", "contract_type": "indefinite"}
    equipment = {"location": "Planta", "criticality": "high"}
    project = {"manager": "Responsable", "start_date": "2026-01-01", "end_date": "2099-12-31", "budget": "1000", "currency": "PEN"}
    freight = {"origin": "Lima", "destination": "Callao", "planned_date": "2026-12-01", "weight_kg": "500", "cargo_description": "Carga"}
    samples = {
        "employees": employee,
        "equipment": equipment,
        "vehicles": {"license_plate": "ABC-123", "capacity_kg": "1000", "vehicle_type": "truck"},
        "freight_orders": freight,
        "bills_of_material": {"product_id": core["product"], "base_quantity": "2", "components": [{"product_id": core["component"], "quantity": "3"}]},
        "inspections": {"product_id": core["product"], "inspection_date": "2026-12-01", "inspected_quantity": "10"},
        "projects": project,
        "fixed_assets": {"acquisition_date": "2026-12-01", "acquisition_cost": "1000", "currency": "PEN", "useful_life_months": 60, "location": "Lima", "custodian": "Responsable"},
        "budgets": {"cost_center_id": core["cost_center"], "start_date": "2026-01-01", "end_date": "2026-12-31", "amount": "100", "currency": "PEN"},
        "opportunities": {"company_name": "Evaluación ñ", "estimated_value": "100", "currency": "PEN", "probability": 50, "expected_close": "2026-12-01"},
        "safety_incidents": {"occurred_on": "2026-12-01", "location": "Planta", "severity": "medium", "description": "Observación"},
        "service_tickets": {"customer_id": core["customer"], "reported_on": "2026-12-01", "priority": "medium", "description": "Evaluación técnica"},
        "warehouse_tasks": {"product_id": core["product"], "warehouse_id": core["warehouse"], "quantity": 2, "task_type": "picking", "source_bin": "A", "destination_bin": "B"},
        "requisitions": {"product_id": core["product"], "quantity": 2, "needed_on": "2026-12-01", "department": "Producción", "reason": "Reposición"},
        "sales_contracts": {"customer_id": core["customer"], "start_date": "2026-01-01", "end_date": "2026-12-31", "amount": "100", "currency": "PEN", "terms": "Entrega en almacén"},
    }
    if code in samples: return samples[code]
    if code in {"absences", "attendance"}:
        employee_id = seed(suite, "employees", employee, "active")
        return {"employee_id": employee_id, **({"start_date": "2026-12-01", "end_date": "2026-12-03", "absence_type": "vacation", "reason": "Descanso"} if code == "absences" else {"work_date": "2026-12-01", "hours": "8"})}
    if code.startswith("maintenance_"):
        equipment_id = seed(suite, "equipment", equipment, "active")
        return {"equipment_id": equipment_id, **({"reported_on": "2026-12-01", "priority": "high", "symptom": "Vibración"} if code.endswith("notifications") else {"planned_start": "2026-12-01", "planned_end": "2026-12-03", "technician": "Técnico", "work_scope": "Revisión"})}
    if code == "trips":
        return {"freight_order_id": seed(suite, "freight_orders", freight, "released"), "vehicle_id": seed(suite, "vehicles", samples["vehicles"], "active"), "driver": "Conductor", "departure_date": "2026-12-01"}
    if code == "production_orders":
        return {"bom_id": seed(suite, "bills_of_material", samples["bills_of_material"], "active"), "quantity": "10", "planned_start": "2026-12-01", "planned_end": "2026-12-03"}
    if code == "nonconformities":
        return {"inspection_id": seed(suite, "inspections", samples["inspections"]), "severity": "major", "finding": "Defecto"}
    if code == "project_tasks":
        return {"project_id": seed(suite, "projects", project, "released"), "assigned_to": "Técnico", "due_date": "2026-12-01", "progress": "0"}
    if code == "supplier_quotes":
        return {"requisition_id": seed(suite, "requisitions", samples["requisitions"], "approved"), "supplier_id": core["supplier"], "unit_price": "7", "currency": "PEN", "valid_until": "2099-12-31", "lead_days": 3}
    raise AssertionError(code)


def create(suite, code, data=None, number=None, expected=201):
    response = suite[0].post(f"/api/v1/enterprise/{code}/records", json={"number": number or str(uuid4())[:8], "title": "Evaluación de operación: ñ á é í ó ú", "data": data if data is not None else sample(suite, code)})
    assert response.status_code == expected, response.text
    return response.json()


def change(suite, row, target, completion=None, expected=200, version=None):
    response = suite[0].post(f"/api/v1/enterprise/records/{row['id']}/transition", json={"version": version or row["version"], "to_state": target, "note": "Revisión y ejecución", "completion": completion or {}})
    assert response.status_code == expected, response.text
    return response.json()


@pytest.mark.parametrize("code", list(RESOURCE_BY_CODE))
def test_each_transaction_persists_unicode_and_is_tenant_scoped(suite, code):
    client, _, state, other, _ = suite
    row = create(suite, code)
    detail = client.get(f"/api/v1/enterprise/records/{row['id']}").json()
    assert detail["title"] == "Evaluación de operación: ñ á é í ó ú"
    assert detail["history"][0]["version"] == 1
    assert len(detail["history"][0]["content_hash"]) == 64
    listing = client.get(f"/api/v1/enterprise/{code}/records?q={row['number']}").json()
    assert listing["meta"]["total"] == 1
    actor_as(state, tenant=other)
    assert client.get(f"/api/v1/enterprise/records/{row['id']}").status_code == 404
    assert client.get(f"/api/v1/enterprise/{code}/records").json()["meta"]["total"] == 0
    assert client.post(f"/api/v1/enterprise/records/{row['id']}/transition", json={"version": 1, "to_state": "cancelled", "note": "Intento ajeno"}).status_code == 404


def test_unicode_edit_versions_and_duplicate_rollback(suite):
    client, db, _, _, _ = suite
    row = create(suite, "equipment", number="EQ-001")
    create(suite, "equipment", number="EQ-001", expected=409)
    assert db.scalar(select(func.count()).select_from(EnterpriseRecordEvent)) == 1
    body = {"number": row["number"], "title": "Evaluación corregida ñ", "data": row["data"], "version": 1}
    edit = client.post(f"/api/v1/enterprise/records/{row['id']}/edit", json=body)
    assert edit.status_code == 200 and edit.json()["version"] == 2
    assert client.post(f"/api/v1/enterprise/records/{row['id']}/edit", json=body).status_code == 409
    detail = client.get(f"/api/v1/enterprise/records/{row['id']}").json()
    assert detail["title"] == body["title"]
    assert [event["version"] for event in detail["history"]] == [1, 2]


@pytest.mark.parametrize("role", list(SPECIALIZED_ROLES))
def test_specialists_receive_only_their_business_documents(suite, role):
    client, _, state, _, _ = suite
    actor_as(state, role=role)
    catalog = client.get("/api/v1/enterprise/catalog").json()
    module = SPECIALIZED_ROLES[role][1]
    assert catalog["ready"]
    assert {row["module"] for row in catalog["resources"]} == {module}
    forbidden = "equipment" if role == "hr" else "employees"
    assert client.get(f"/api/v1/enterprise/{forbidden}/records").status_code == 403
    assert client.get(f"/api/v1/enterprise/{forbidden}/options/employee_id").status_code == 403


def test_disabled_module_cannot_be_used_even_by_owner(suite, monkeypatch):
    monkeypatch.setattr(auth, "module_is_enabled", lambda *_: False)
    assert suite[0].get("/api/v1/enterprise/employees/records").status_code == 403


def test_missing_schema_exposes_activation_instead_of_false_success(suite):
    client, db, _, _, _ = suite
    EnterpriseRecordEvent.__table__.drop(db.connection())
    EnterpriseRecord.__table__.drop(db.connection()); db.commit()
    catalog = client.get("/api/v1/enterprise/catalog").json()
    assert not catalog["ready"] and catalog["migration"] == "0065_enterprise_modules.sql"
    create(suite, "equipment", expected=503)


def test_approval_requires_mfa_and_an_independent_approver(suite):
    _, _, state, _, _ = suite
    row = create(suite, "employees")
    change(suite, row, "active", expected=403)
    actor_as(state, user=uuid4(), role="hr", assurance="aal1")
    change(suite, row, "active", expected=403)
    actor_as(state, assurance="aal2")
    active = change(suite, row, "active")
    assert active["status"] == "active" and active["version"] == 2
    change(suite, active, "draft", expected=409)


@pytest.mark.parametrize("code,patch", [
    ("employees", {"work_email": "invalid"}),
    ("employees", {"payroll_secret": "injected"}),
    ("projects", {"end_date": "2025-01-01"}),
    ("opportunities", {"probability": 101}),
    ("vehicles", {"capacity_kg": "NaN"}),
    ("fixed_assets", {"useful_life_months": 1.5}),
    ("warehouse_tasks", {"quantity": 1.2}),
    ("warehouse_tasks", {"destination_bin": "a"}),
    ("bills_of_material", {"components": []}),
])
def test_invalid_business_values_never_persist(suite, code, patch):
    data = sample(suite, code)
    create(suite, code, {**data, **patch}, expected=422)
    assert suite[1].scalar(select(func.count()).select_from(EnterpriseRecord)) == 0


def test_core_references_and_reference_options_do_not_cross_tenants(suite):
    client, _, state, other, core = suite
    options = client.get("/api/v1/enterprise/bills_of_material/options/product_id?q=PRODUCT").json()
    assert options[0]["id"] == core["product"]
    data = sample(suite, "service_tickets")
    actor_as(state, tenant=other)
    assert client.get("/api/v1/enterprise/bills_of_material/options/product_id").json() == []
    create(suite, "service_tickets", data, expected=422)


def test_employee_journal_duplicates_and_overlapping_approved_absence(suite):
    _, _, state, _, _ = suite
    data = sample(suite, "attendance")
    create(suite, "attendance", data)
    create(suite, "attendance", data, expected=409)
    absence = {"employee_id": data["employee_id"], "start_date": "2026-12-01", "end_date": "2026-12-03", "absence_type": "vacation", "reason": "Descanso"}
    first = change(suite, create(suite, "absences", absence), "submitted")
    second = change(suite, create(suite, "absences", absence), "submitted")
    actor_as(state, user=uuid4())
    change(suite, first, "approved")
    change(suite, second, "approved", expected=409)


def test_production_snapshots_component_requirements_and_does_not_change_cost(suite):
    _, db, state, _, _ = suite
    order = create(suite, "production_orders")
    actor_as(state, user=uuid4(), role="production")
    released = change(suite, order, "released")
    assert released["derived"]["bom_version"] == 1
    assert released["derived"]["requirements"][0]["quantity"] == "15"
    assert all(item.average_cost == 7 for item in db.scalars(select(Product)).all())
    running = change(suite, released, "in_progress")
    change(suite, running, "completed", expected=422)
    done = change(suite, running, "completed", {"actual_quantity": "9", "completion_note": "Producción terminada"})
    assert done["derived"] == released["derived"]


def test_inspection_cannot_close_with_unreconciled_quantities(suite):
    row = change(suite, create(suite, "inspections"), "in_progress")
    change(suite, row, "completed", {"accepted_quantity": "8", "rejected_quantity": "1", "disposition": "Revisado"}, expected=422)
    assert change(suite, row, "completed", {"accepted_quantity": "8", "rejected_quantity": "2", "disposition": "Revisado"})["status"] == "completed"


def test_trip_capacity_vehicle_assignment_and_required_delivery_evidence(suite):
    _, db, _, _, _ = suite
    data = sample(suite, "trips")
    vehicle = db.get(EnterpriseRecord, UUID(data["vehicle_id"]))
    vehicle.data = {**vehicle.data, "capacity_kg": "100"}; db.commit()
    first = create(suite, "trips", data)
    change(suite, first, "dispatched", expected=422)
    vehicle.data = {**vehicle.data, "capacity_kg": "1000"}; db.commit()
    dispatched = change(suite, first, "dispatched")
    second = create(suite, "trips", data)
    change(suite, second, "dispatched", expected=409)
    change(suite, dispatched, "delivered", expected=422)
    change(suite, dispatched, "delivered", {"arrival_date": "2026-12-02", "proof_reference": "Entrega firmada"})
    assert change(suite, second, "dispatched")["status"] == "dispatched"


def test_project_cannot_close_while_children_are_unfinished(suite):
    client, db, _, _, _ = suite
    data = sample(suite, "project_tasks")
    child = create(suite, "project_tasks", data)
    parent = client.get(f"/api/v1/enterprise/records/{data['project_id']}").json()
    parent = change(suite, parent, "in_progress")
    change(suite, parent, "completed", expected=409)
    child = change(suite, child, "in_progress")
    change(suite, child, "completed", expected=422)
    edit = client.post(f"/api/v1/enterprise/records/{child['id']}/edit", json={"number": child["number"], "title": child["title"], "version": child["version"], "data": {**child["data"], "progress": "100"}})
    assert edit.status_code == 200
    change(suite, edit.json(), "completed")
    assert change(suite, parent, "completed")["status"] == "completed"
    assert db.scalar(select(func.count()).select_from(EnterpriseRecordEvent)) == 6


def test_quote_selection_and_purchase_order_closure(suite):
    client, _, state, _, core = suite
    data = sample(suite, "supplier_quotes")
    first = create(suite, "supplier_quotes", data)
    second = create(suite, "supplier_quotes", data)
    actor_as(state, user=uuid4())
    change(suite, first, "selected")
    change(suite, second, "selected", expected=409)
    request = client.get(f"/api/v1/enterprise/records/{data['requisition_id']}").json()
    change(suite, request, "closed", expected=422)
    assert change(suite, request, "closed", {"purchase_order_id": core["purchase_order"]})["status"] == "closed"


def test_closure_cannot_inject_other_business_fields(suite):
    row = create(suite, "safety_incidents")
    row = change(suite, row, "in_progress")
    change(suite, row, "resolved", {"corrective_action": "Protección instalada", "severity": "low"}, expected=422)
    resolved = change(suite, row, "resolved", {"corrective_action": "Protección instalada"})
    assert resolved["data"]["severity"] == "medium"
