import io
from zipfile import ZipFile, ZIP_STORED

from openpyxl import Workbook, load_workbook
from sqlalchemy import select

from test_migration_projects import suite, create, simulate, upload, count, commit
from app.models.entities import UnitOfMeasure, Product
from app.models.enterprise import EnterpriseRecord
from app.models.migration_projects import MigrationObject
from app.services.migration_catalog import DEFINITIONS
from app.services.migration_examples import example_rows
from app.services.migration_files import SHEET_NAMES


def excel(objects):
    book = Workbook(); book.remove(book.active)
    for sheet_name, columns, rows in objects:
        sheet = book.create_sheet(sheet_name); sheet.append(columns)
        for row in rows: sheet.append([row.get(key, "") for key in columns])
    stream = io.BytesIO(); book.save(stream); book.close()
    return stream.getvalue()


def sheet(code, rows=None):
    return SHEET_NAMES[code], DEFINITIONS[code]["columns"], rows if rows is not None else example_rows()[code]


def bulk(suite, project, files):
    return suite[0].post(f"/api/v1/migration-projects/{project['id']}/upload-bulk", data={"version": project["version"]}, files=[("files", (name, content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")) for name, content in files])


def test_multisheet_excel_auto_assigns_and_simulates_complete_dependency_order(suite):
    project = create(suite, ["base"])
    response = bulk(suite, project, [("MM - Materiales y abastecimiento.xlsx", excel([sheet("products"), sheet("units"), sheet("suppliers")]))])
    assert response.status_code == 200, response.text
    project = response.json()
    assert len(project["simulation"]["upload_logs"]) == 3
    assert {item["code"] for item in project["objects"] if item["rows"]} == {"products", "units", "suppliers"}
    project = simulate(suite, project)
    assert project["simulation"]["can_commit"], project["simulation"]
    assert count(suite[1], Product) == 0
    assert count(suite[1], UnitOfMeasure) == 0
    assert project["simulation"]["objects"]["products"]["logs"][0]["severity"] == "success"


def test_invalid_file_rejects_whole_batch_with_field_location_and_keeps_previous_upload(suite):
    project = upload(suite, create(suite, ["base"]), "units")
    bad = excel([("Clientes", ["code", "columna_inventada"], [{"code": "C", "columna_inventada": "error"}])])
    response = bulk(suite, project, [("Proveedores.xlsx", excel([sheet("suppliers")])), ("SD.xlsx", bad)])
    assert response.status_code == 422
    logs = response.json()["detail"]["logs"]
    assert any(item["field"] == "columna_inventada" and item["row"] == 1 and item["sheet"] == "Clientes" for item in logs)
    current = suite[0].get(f"/api/v1/migration-projects/{project['id']}").json()
    assert current["version"] == project["version"]
    assert {row.object_code for row in suite[1].scalars(select(MigrationObject))} == {"units"}


def test_unknown_ambiguous_file_and_duplicate_objects_are_rejected(suite):
    project = create(suite, ["base"])
    ambiguous = excel([("Hoja1", ["code", "name"], [{"code": "A", "name": "Unidad"}])])
    assert bulk(suite, project, [("Desconocido.xlsx", ambiguous)]).status_code == 422
    response = bulk(suite, project, [("Unidades de medida.xlsx", excel([sheet("units")])), ("Copia.xlsx", excel([sheet("units")]))])
    assert response.status_code == 422
    assert "más de una vez" in response.json()["detail"]["logs"][0]["message"]
    assert count(suite[1], MigrationObject) == 0


def test_unambiguous_headers_can_recognize_renamed_file_and_formula_is_rejected(suite):
    project = create(suite, ["base"])
    content = excel([("Plantilla", DEFINITIONS["customers"]["columns"], example_rows()["customers"])])
    result = bulk(suite, project, [("Cartera nueva.xlsx", content)])
    assert result.status_code == 200, result.text
    assert "columnas únicas" in result.json()["simulation"]["upload_logs"][0]["message"]
    formula = excel([sheet("units", [{"code": "KG", "name": "=1+1"}])])
    result = bulk(suite, result.json(), [("Unidades.xlsx", formula)])
    assert result.status_code == 422
    assert result.json()["detail"]["logs"][0]["field"] == "name"


def test_large_xlsx_above_old_10mb_limit_is_accepted_but_bounded(suite):
    content = excel([sheet("units")])
    expanded = io.BytesIO()
    with ZipFile(io.BytesIO(content)) as source, ZipFile(expanded, "w", ZIP_STORED) as target:
        for name in source.namelist(): target.writestr(name, source.read(name))
        target.writestr("padding.txt", b" " * (11 * 1024 * 1024))
    response = bulk(suite, create(suite, ["base"]), [("Unidades de medida.xlsx", expanded.getvalue())])
    assert response.status_code == 200, response.text


def test_all_enterprise_row_errors_keep_sheet_field_and_value(suite):
    project = create(suite, ["tm"])
    first = example_rows()["vehicles"][0]
    rows = [{**first, "number": "BAD1", "capacity_kg": -1}, {**first, "number": "BAD2", "capacity_kg": -5}]
    response = bulk(suite, project, [("TM - Transporte.xlsx", excel([sheet("vehicles", rows)]))])
    assert response.status_code == 200, response.text
    project = simulate(suite, response.json())
    errors = project["simulation"]["objects"]["vehicles"]["errors"]
    assert {item["row"] for item in errors} == {2, 3}
    assert all(item["file"] == "TM - Transporte.xlsx" and item["sheet"] == "Vehículos" and item["field"] == "capacity_kg" for item in errors)
    assert not project["simulation"]["can_commit"]
    assert count(suite[1], EnterpriseRecord) == 0
    commit(suite, project, 409, digest="0" * 64)


def test_group_workbook_exports_spanish_sheets_and_roundtrips(suite):
    project = create(suite, ["pm"])
    response = suite[0].get(f"/api/v1/migration-projects/{project['id']}/workbook?example=true")
    assert response.status_code == 200
    book = load_workbook(io.BytesIO(response.content), read_only=True)
    assert "Equipos técnicos" in book.sheetnames
    book.close()
    response = bulk(suite, project, [("PM - Mantenimiento.xlsx", response.content)])
    assert response.status_code == 200, response.text
    assert simulate(suite, response.json())["simulation"]["can_commit"]


def test_disabled_specialty_and_stale_version_reject_bulk(suite):
    project = create(suite, ["base"])
    assert bulk(suite, project, [("HCM.xlsx", excel([sheet("employees")]))]).status_code == 422
    response = bulk(suite, project, [("Unidades de medida.xlsx", excel([sheet("units")]))])
    assert response.status_code == 200
    assert bulk(suite, project, [("Unidades de medida.xlsx", excel([sheet("units")]))]).status_code == 409


def test_core_material_validation_locates_each_invalid_profile_cell(suite):
    project = create(suite, ["base"])
    first = example_rows()["products"][0]
    rows = [{**first, "sku": "BAD-A", "net_weight": -1}, {**first, "sku": "BAD-B", "lead_time_days": -2}]
    response = bulk(suite, project, [("Materiales y productos.xlsx", excel([sheet("products", rows), sheet("units")]))])
    assert response.status_code == 200, response.text
    result = simulate(suite, response.json())
    errors = result["simulation"]["objects"]["products"]["errors"]
    assert {(error["row"], error["field"]) for error in errors} == {(2, "net_weight"), (3, "lead_time_days")}
    assert all(error["value"] in {"-1", "-2"} for error in errors)
    assert not result["simulation"]["can_commit"]
    assert count(suite[1], Product) == 0


def test_invalid_units_report_all_rows_without_losing_their_field_names(suite):
    rows = [{"code": "", "name": ""}, {"code": "WAY-TOO-LONG-UNIT-CODE", "name": "Unidad"}]
    response = bulk(suite, create(suite, ["base"]), [("Unidades de medida.xlsx", excel([sheet("units", rows)]))])
    assert response.status_code == 200
    result = simulate(suite, response.json())
    errors = result["simulation"]["objects"]["units"]["errors"]
    # A completely blank row is deliberately ignored, never imported.
    assert {(error["row"], error["field"]) for error in errors} == {(3, "code")}
    assert count(suite[1], UnitOfMeasure) == 0
