"""Workbook presentation and safe exports, independent from import mutations."""

import csv
import io
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill


def csv_bytes(columns: list[str], rows: list[list[Any]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(columns)
    for row in rows:
        # CSV has no text cell type. Neutralize spreadsheet formula execution.
        writer.writerow(["'" + str(value) if str(value).lstrip().startswith(("=", "+", "-", "@")) else value for value in row])
    return output.getvalue().encode("utf-8-sig")


def _append(sheet, row):
    sheet.append(["" if value is None else str(value) for value in row])
    for cell in sheet[sheet.max_row]:
        cell.data_type = "s"  # IDs/barcodes remain text; never create formulas.


def workbook_bytes(template: dict, *, rows: list[list[Any]] | None = None, catalogs: list[list[str]] | None = None) -> bytes:
    book = Workbook()
    data = book.active
    data.title = "Plantilla"
    _append(data, template["columns"])
    for row in rows or []:
        _append(data, row)
    data.freeze_panes = "C2"
    data.auto_filter.ref = data.dimensions
    instructions = book.create_sheet("Instrucciones")
    for row in [
        ["PLANTILLA", template["label"]],
        ["Versión", template.get("version", "1.0")],
        ["Preparación", "Completa Plantilla. Consulta Ejemplo y Diccionario. El ejemplo separado no se importa."],
        ["Lugar de carga", "Centro de migraciones: crea un grupo y usa Acciones > Cargar archivo junto al objeto correspondiente."],
        ["Simulación", "Simular grupo revisa todos los archivos y referencias en secuencia. No deja registros empresariales."],
        ["Confirmación", "Revisa el resumen y marca el check final. El grupo se guarda completo o se revierte completo. Centro de migraciones: hasta 12000 filas por grupo / 20 MB por archivo."],
        ["Verificación", "Revisa los módulos y conserva el comprobante del grupo con su hash SHA-256."],
        ["Modo de carga", "Solo altas; un código existente genera conflicto. La exportación sirve para revisión, no para sobrescribir datos."],
        ["Existencias", "El material se carga una sola vez. Para stock, añade la plantilla Existencias al grupo con almacén y motivo."],
        ["Formato", "No cambies encabezados. Usa valores, sin fórmulas. Conserva códigos como texto y decimales con punto."],
        ["Datos industriales", "Los datos de planificación son informativos. No crean órdenes, reservas, MRP ni asientos."],
    ]:
        _append(instructions, row)
    dictionary = book.create_sheet("Diccionario")
    _append(dictionary, ["Campo", "Grupo", "Valor obligatorio", "Regla"])
    for field in template.get("fields", [{"key": key, "group": template["label"], "description": f"Conserva el encabezado {key}. Consulta el formato en la hoja Ejemplo."} for key in template["columns"]]):
        _append(dictionary, [field["key"], field["group"], "Sí" if field.get("required") else "No", field["description"]])
    example = book.create_sheet("Ejemplo")
    _append(example, template["columns"])
    _append(example, template["example"])
    reference = book.create_sheet("Catalogos")
    _append(reference, ["Catálogo", "Código", "Descripción"])
    for row in catalogs or []:
        _append(reference, row)
    for sheet in book:
        sheet.sheet_view.showGridLines = False
        sheet.freeze_panes = sheet.freeze_panes or "A2"
        for cell in sheet[1]:
            cell.fill = PatternFill("solid", fgColor="163E55")
            cell.font = Font(color="FFFFFF", bold=True)
        for column in sheet.columns:
            letter = column[0].column_letter
            sheet.column_dimensions[letter].width = min(65, max(20, len(str(column[0].value or "")) + 3))
        if sheet.title in {"Instrucciones", "Diccionario"}:
            sheet.column_dimensions["B" if sheet.title == "Instrucciones" else "D"].width = 100
            for row in sheet.iter_rows(min_row=2):
                for cell in row:
                    cell.alignment = Alignment(vertical="top", wrap_text=True)
                sheet.row_dimensions[row[0].row].height = 42
    output = io.BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def group_workbook_bytes(templates, examples=None):
    from app.services.migration_files import SHEET_NAMES
    book = Workbook()
    book.remove(book.active)
    for template in templates:
        code = template["code"]
        sheet = book.create_sheet(SHEET_NAMES[code])
        _append(sheet, template["columns"])
        for row in (examples or {}).get(code, []): _append(sheet, [row.get(key, "") for key in template["columns"]])
        sheet.freeze_panes = "C2"
        sheet.auto_filter.ref = sheet.dimensions
        sheet.sheet_view.showGridLines = False
        for cell in sheet[1]:
            cell.fill = PatternFill("solid", fgColor="163E55")
            cell.font = Font(name="Arial", color="FFFFFF", bold=True)
            sheet.column_dimensions[cell.column_letter].width = max(20, min(48, len(str(cell.value)) + 3))
    instructions = book.create_sheet("Instrucciones")
    for row in [["Uso", "Cada hoja es una plantilla. Mantén su nombre y encabezados; carga el libro desde Carga masiva."], ["Carga", "El reconocimiento asigna las hojas al grupo. Después simula, revisa el Registro de validación y pulsa Cargar datos."], ["Límites", "20 MB por Excel; 40 MB por envío; 5000 filas por hoja y 12000 filas por grupo."], ["Ejemplos", "Datos ficticios relacionados; no son operaciones reales de una empresa."]]: _append(instructions, row)
    instructions.column_dimensions["A"].width = 18
    instructions.column_dimensions["B"].width = 115
    output = io.BytesIO()
    book.save(output); book.close()
    return output.getvalue()
