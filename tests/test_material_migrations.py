"""Exercise the material lifecycle against an isolated relational database.

SQLite covers the API/unit of work here. PostgreSQL RLS, row locks and the SQL
migration still require a staging smoke test before production deployment.
"""

import io
from hashlib import sha256
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from app.security.action_confirmation import enforce_action_confirmation
from openpyxl import Workbook, load_workbook
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.exc import IntegrityError

from app.api.routes import imports, products
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.entities import Category, Product, ProductMaterialProfile, Tenant, UnitOfMeasure
from app.security import auth
from app.security.auth import Actor, get_actor
from app.services.material_master import material_template
from app.services.migration_workbooks import csv_bytes, workbook_bytes


@compiles(JSONB, "sqlite")
def sqlite_jsonb(_type, _compiler, **_kwargs):
    return "JSON"


def row(**changes):
    return {"sku": "MAT-001", "name": "Alambre", "category": "Metales", "unit": "NIU", "sale_price": "100.00", "average_cost": "80.00", **changes}


def csv_file(rows):
    columns = list(dict.fromkeys(key for item in rows for key in item))
    return csv_bytes(columns, [[item.get(key, "") for key in columns] for item in rows])


@pytest.fixture
def migration_client(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    tables = [model.__table__ for model in (Tenant, Category, UnitOfMeasure, Product, ProductMaterialProfile)]
    Base.metadata.create_all(engine, tables=tables)
    state = {"actor": Actor(user_id=uuid4(), tenant_id=uuid4(), role_code="owner", assurance_level="aal2")}
    other_tenant = uuid4()
    with Session(engine) as db:
        db.add_all([Tenant(id=state["actor"].tenant_id, name="A"), Tenant(id=other_tenant, name="B")])
        db.flush()
        db.add_all([UnitOfMeasure(tenant_id=state["actor"].tenant_id, code="NIU", name="Unidad"), UnitOfMeasure(tenant_id=other_tenant, code="KGM", name="Kilogramo")])
        db.commit()

        def database():
            try:
                yield db
            finally:
                # Same rollback-on-close behavior as the production dependency.
                db.rollback()

        app.dependency_overrides[get_actor] = lambda: state["actor"]
        app.dependency_overrides[enforce_action_confirmation] = lambda: None
        app.dependency_overrides[get_db] = database
        monkeypatch.setattr(auth, "module_is_enabled", lambda *_: True)
        monkeypatch.setattr(imports, "record", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(products, "record", lambda *_args, **_kwargs: None)
        with TestClient(app) as client:
            yield client, db, state, other_tenant
        app.dependency_overrides.clear()
    engine.dispose()


@pytest.mark.parametrize("changes", [
    {"sale_price": "-1"}, {"average_cost": "NaN"}, {"average_cost": "Infinity"},
    {"tracking_mode": "invalid"}, {"cost_method": "invalid"}, {"is_active": "invalid"},
    {"net_weight": "2", "gross_weight": "1", "weight_unit": "KG"},
    {"net_weight": "2"}, {"lead_time_days": "1.5"}, {"safety_stock": "-1"},
    {"country_of_origin": "PER"}, {"hs_code": "abc"}, {"unexpected_column": "value"},
])
def test_invalid_industrial_data_is_rejected_before_writing(changes):
    valid, errors = imports._validate_rows("products", [row(**changes)])
    assert not valid and errors


def test_legacy_six_column_contract_is_preserved():
    valid, errors = imports._validate_rows("products", [row(unit="unit")])
    assert not errors
    assert valid[0]["tracking_mode"] == "none"
    assert valid[0]["is_active"] is True
    assert valid[0]["material_profile"] == {}


@pytest.mark.parametrize("module", list(imports.TEMPLATES))
def test_each_existing_template_example_remains_valid(module):
    template = imports.TEMPLATES[module]
    valid, errors = imports._validate_rows(module, [dict(zip(template["columns"], template["example"]))])
    assert errors == []
    assert len(valid) == 1


@pytest.mark.parametrize("module,field", [("sales", "unit_price"), ("purchases", "unit_cost"), ("customers", "credit_limit")])
def test_monetary_values_cannot_bypass_domain_rules_through_import(module, field):
    template = imports.TEMPLATES[module]
    data = dict(zip(template["columns"], template["example"]))
    data[field] = "-1"
    valid, errors = imports._validate_rows(module, [data])
    assert not valid and errors


def test_duplicates_report_the_source_row_after_an_invalid_row():
    _, errors = imports._validate_rows("products", [row(name="", __source_row__=2), row(__source_row__=4), row(sku="mat-001", __source_row__=7)])
    assert [error["row"] for error in errors] == [2, 7]


@pytest.mark.asyncio
async def test_reader_uses_plantilla_even_when_instructions_are_active():
    content = workbook_bytes(material_template(), rows=[list(row().values())])
    book = load_workbook(io.BytesIO(content))
    book.active = 1
    output = io.BytesIO()
    book.save(output)
    result = await imports._rows_from_upload(UploadFile(filename="materiales.xlsx", file=io.BytesIO(output.getvalue())))
    assert result[0]["sku"] == "MAT-001"
    assert result[0]["__source_row__"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"sku,sku\nA,B\n", b"sku,name\nA,B,extra\n"])
async def test_reader_rejects_duplicate_headers_and_unheaded_values(content):
    with pytest.raises(HTTPException):
        await imports._rows_from_upload(UploadFile(filename="x.csv", file=io.BytesIO(content)))


@pytest.mark.asyncio
async def test_reader_rejects_xlsx_formulas():
    book = Workbook()
    book.active.append(["sku", "name"])
    book.active.append(["A", "=1+1"])
    output = io.BytesIO()
    book.save(output)
    with pytest.raises(HTTPException, match="fórmulas"):
        await imports._rows_from_upload(UploadFile(filename="x.xlsx", file=io.BytesIO(output.getvalue())))


def test_preview_commit_profile_and_export_are_consistent(migration_client):
    client, db, state, other_tenant = migration_client
    content = csv_file([row(tracking_mode="lot", barcode="000123", lead_time_days="7", net_weight="1.500", gross_weight="1.600", weight_unit="KG")])
    preview = client.post("/api/v1/imports/products/preview", files={"file": ("materials.csv", content)})
    assert preview.status_code == 200, preview.text
    assert preview.json()["can_commit"] is True
    assert db.scalar(select(Product)) is None  # Preview has no business writes.
    digest = preview.json()["file_sha256"]
    response = client.post("/api/v1/imports/products/commit", files={"file": ("materials.csv", content)}, data={"preview_digest": digest})
    assert response.status_code == 201, response.text
    assert response.json()["file_sha256"] == sha256(content).hexdigest()
    product = db.scalar(select(Product))
    assert product.tracking_mode == "lot"
    assert db.scalar(select(ProductMaterialProfile)).attributes["barcode"] == "000123"
    exported = client.get("/api/v1/imports/products/export?format=xlsx")
    assert exported.status_code == 200
    book = load_workbook(io.BytesIO(exported.content))
    values = list(book["Plantilla"].values)
    assert len(values) == 2 and len(values[0]) == 25
    assert values[1][values[0].index("barcode")] == "000123"
    product_id = product.id
    state["actor"] = Actor(user_id=uuid4(), tenant_id=other_tenant, role_code="owner", assurance_level="aal2")
    assert client.get(f"/api/v1/products/{product.id}/material-profile").status_code == 404
    assert client.put(f"/api/v1/products/{product_id}/material-profile", json={"brand": "Cross-tenant write"}).status_code == 404
    exported_b = client.get("/api/v1/imports/products/export?format=xlsx")
    assert load_workbook(io.BytesIO(exported_b.content))["Plantilla"].max_row == 1


def test_material_profile_can_be_edited_without_replacing_core_product(migration_client):
    client, db, _, _ = migration_client
    response = client.post("/api/v1/imports/products/commit", files={"file": ("x.csv", csv_file([row()]))})
    assert response.status_code == 201
    product = db.scalar(select(Product))
    response = client.put(f"/api/v1/products/{product.id}/material-profile", json={"barcode": "000999", "safety_stock": 12})
    assert response.status_code == 200
    assert client.get(f"/api/v1/products/{product.id}/material-profile").json()["attributes"]["barcode"] == "000999"
    assert db.scalar(select(Product)).sku == "MAT-001"


def test_composite_fk_rejects_profile_linking_different_tenants(migration_client):
    client, db, _, other_tenant = migration_client
    assert client.post("/api/v1/imports/products/commit", files={"file": ("x.csv", csv_file([row()]))}).status_code == 201
    product = db.scalar(select(Product))
    db.add(ProductMaterialProfile(product_id=product.id, tenant_id=other_tenant, attributes={"brand": "Invalid"}))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_preview_cannot_resolve_a_unit_from_another_tenant(migration_client):
    client, db, _, _ = migration_client
    content = csv_file([row(unit="KGM")])
    response = client.post("/api/v1/imports/products/preview", files={"file": ("x.csv", content)})
    assert response.status_code == 200
    assert not response.json()["can_commit"]
    assert "KGM" in response.json()["conflicts"][0]
    assert db.scalar(select(Product)) is None


def test_commit_rejects_a_changed_file(migration_client):
    client, db, _, _ = migration_client
    response = client.post("/api/v1/imports/products/commit", files={"file": ("x.csv", csv_file([row()]))}, data={"preview_digest": "0" * 64})
    assert response.status_code == 409
    assert db.scalar(select(Product)) is None


def test_whole_batch_rolls_back_after_a_persistence_failure(migration_client, monkeypatch):
    client, db, _, _ = migration_client
    calls = 0
    def fail_audit(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated failure after the second product flush")
    monkeypatch.setattr(imports, "record", fail_audit)
    response = client.post("/api/v1/imports/products/commit", files={"file": ("x.csv", csv_file([row(), row(sku="MAT-002", category="Otra categoría")]))})
    assert response.status_code == 500
    assert db.scalar(select(Product)) is None
    assert db.scalar(select(Category)) is None


def test_data_routes_respect_a_disabled_product_module(migration_client, monkeypatch):
    client, db, _, _ = migration_client
    monkeypatch.setattr(auth, "module_is_enabled", lambda *_: False)
    content = csv_file([row()])
    assert client.post("/api/v1/imports/products/preview", files={"file": ("x.csv", content)}).status_code == 403
    assert client.get("/api/v1/imports/products/template").status_code == 403
    assert client.get("/api/v1/imports/products/export").status_code == 403
    assert db.scalar(select(Product)) is None


def test_release_before_migration_preserves_core_imports_and_exports(migration_client):
    client, db, _, _ = migration_client
    # Deliberately simulate an older schema in this isolated SQLite database.
    ProductMaterialProfile.__table__.drop(db.get_bind())
    metadata = client.get("/api/v1/imports/templates")
    assert metadata.status_code == 200
    template = next(item for item in metadata.json() if item["module"] == "products")
    assert len(template["columns"]) == 9
    assert "barcode" not in template["columns"]
    assert client.get("/api/v1/imports/products/template").status_code == 200
    uploaded = client.post("/api/v1/imports/products/commit", files={"file": ("x.csv", csv_file([row()]))})
    assert uploaded.status_code == 201
    assert client.get("/api/v1/imports/products/export?format=xlsx").status_code == 200
    profile_preview = client.post("/api/v1/imports/products/preview", files={"file": ("x.csv", csv_file([row(sku="MAT-002", barcode="000123")]))})
    assert profile_preview.status_code == 200
    assert profile_preview.json()["can_commit"] is False
    product = db.scalar(select(Product))
    assert client.get(f"/api/v1/products/{product.id}/material-profile").status_code == 404


@pytest.mark.parametrize("role,assurance,expected", [("viewer", "aal2", 403), ("inventory", "aal2", 403), ("owner", "aal1", 403)])
def test_bulk_commit_preserves_role_and_second_factor_guards(migration_client, role, assurance, expected):
    client, db, state, _ = migration_client
    original = state["actor"]
    state["actor"] = Actor(user_id=original.user_id, tenant_id=original.tenant_id, role_code=role, assurance_level=assurance)
    response = client.post("/api/v1/imports/products/commit", files={"file": ("x.csv", csv_file([row()]))})
    assert response.status_code == expected
    assert db.scalar(select(Product)) is None


def test_workbook_examples_are_separate_and_text_cells_are_not_formulas():
    template = material_template()
    template["example"][1] = "=HYPERLINK(\"https://example.test\")"
    book = load_workbook(io.BytesIO(workbook_bytes(template)))
    assert book["Plantilla"].max_row == 1
    assert book["Ejemplo"]["B2"].data_type == "s"
    assert book["Ejemplo"]["B2"].value.startswith("=")
    assert "'=1+1" in csv_bytes(["name"], [["=1+1"]]).decode("utf-8-sig")
