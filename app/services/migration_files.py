"""Recognize complete import objects before mutating any staged data."""
import csv
import io
import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePath
from zipfile import BadZipFile, ZipFile

from fastapi import HTTPException
from openpyxl import load_workbook

from app.api.routes.imports import _header
from app.services.migration_catalog import DEFINITIONS

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_BATCH_BYTES = 40 * 1024 * 1024
MAX_EXPANDED_BYTES = 80 * 1024 * 1024
MAX_OBJECT_ROWS = 2000  # Matches the existing 0066 database CHECK constraint.
MAX_PROJECT_ROWS = 12000
MAX_PROJECT_BYTES = 20 * 1024 * 1024
MAX_FILES = 40
SUPPORT_SHEETS = {"instrucciones", "diccionario", "ejemplo", "catalogos", "guia", "resumen", "leeme"}

# Excel has a 31-character worksheet-name limit. These public Spanish names
# are shared by download, automatic recognition and generated workbooks.
SHEET_NAMES = {
    "units": "Unidades de medida", "products": "Materiales y productos", "customers": "Clientes", "suppliers": "Proveedores",
    "warehouses": "Almacenes", "inventory": "Existencias iniciales", "cost_centers": "Centros de costo",
    "sales": "Ventas", "purchases": "Compras", "employees": "Empleados", "absences": "Ausencias y vacaciones",
    "attendance": "Jornadas de trabajo", "sales_contracts": "Acuerdos comerciales", "requisitions": "Solicitudes de compra",
    "supplier_quotes": "Cotizaciones de proveedores", "bills_of_material": "Listas de materiales", "production_orders": "Órdenes de producción",
    "equipment": "Equipos técnicos", "maintenance_notifications": "Avisos de mantenimiento", "maintenance_orders": "Órdenes de mantenimiento",
    "inspections": "Lotes de inspección", "nonconformities": "No conformidades", "warehouse_tasks": "Tareas de almacén",
    "vehicles": "Vehículos", "freight_orders": "Órdenes de transporte", "trips": "Viajes", "projects": "Proyectos",
    "project_tasks": "Tareas de proyecto", "budgets": "Presupuestos", "fixed_assets": "Activos fijos",
    "opportunities": "Oportunidades comerciales", "safety_incidents": "Incidentes de seguridad", "service_tickets": "Solicitudes de servicio",
}


def normalized_name(name):
    value = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", re.sub(r"^\s*\d+\s*[-_. ]+", "", value))


def object_from_name(name):
    key = normalized_name(name)
    found = {code for code, definition in DEFINITIONS.items() if key in {normalized_name(code), normalized_name(definition["label"]), normalized_name(SHEET_NAMES[code])}}
    return next(iter(found)) if len(found) == 1 else None


def issue(filename, sheet, message, *, code=None, row=None, field=None, value=None, severity="error", phase="recognition"):
    return {"phase": phase, "severity": severity, "file": filename, "sheet": sheet, "object": code, "row": row, "field": field, "value": None if value is None else str(value)[:200], "message": message}


@dataclass
class RecognizedObject:
    code: str
    filename: str
    sheet: str
    rows: list
    messages: list


def reject(messages):
    raise HTTPException(status_code=422, detail={"message": "Carga rechazada. Revisa el registro de errores; ningún archivo de este envío fue asignado.", "logs": messages})


def _sheet(values, filename, sheet, allowed, expected=None, multi=False):
    errors = []
    headers = next(values, None)
    if not headers:
        return None, [issue(filename, sheet, "La hoja no contiene encabezados", row=1)]
    headers = list(headers)
    while headers and headers[-1] in (None, ""): headers.pop()
    columns = [_header(value) for value in headers]
    if not columns or len(columns) > 100 or any(not key for key in columns) or len(columns) != len(set(columns)):
        return None, [issue(filename, sheet, "Usa encabezados únicos, completos y hasta 100 columnas", row=1)]
    named = object_from_name(sheet) if multi or normalized_name(sheet) != "plantilla" else None
    named = named or (object_from_name(PurePath(filename).stem) if not multi else None)
    code = expected or named
    signatures = [key for key, definition in DEFINITIONS.items() if set(columns) == set(definition["columns"])]
    if not code:
        code = signatures[0] if len(signatures) == 1 else None
    if not code:
        return None, [issue(filename, sheet, "No es una plantilla reconocida o sus columnas son ambiguas. Conserva el nombre exacto de la hoja o del archivo descargado.", row=1)]
    if code not in allowed:
        return None, [issue(filename, sheet, "La plantilla no pertenece a los módulos asignados a este grupo", code=code)]
    definition = DEFINITIONS[code]
    required = set(definition.get("required_columns", definition["columns"]))
    missing, extra = required - set(columns), set(columns) - set(definition["columns"])
    if missing or extra:
        for key in sorted(missing): errors.append(issue(filename, sheet, "Falta la columna obligatoria", code=code, row=1, field=key))
        for key in sorted(extra): errors.append(issue(filename, sheet, "Columna ajena a esta plantilla", code=code, row=1, field=key))
        return None, errors
    if expected and named and named != expected:
        return None, [issue(filename, sheet, "El nombre identifica otra plantilla; usa Carga masiva para asignarla automáticamente", code=code, row=1)]
    rows = []
    for line, cells in enumerate(values, 2):
        if line > MAX_OBJECT_ROWS + 1:
            errors.append(issue(filename, sheet, f"La hoja admite hasta {MAX_OBJECT_ROWS} filas. Divide el objeto en grupos de carga.", code=code, row=line)); break
        if not any(value not in (None, "") for value in cells): continue
        if any(value not in (None, "") for value in cells[len(columns):]):
            errors.append(issue(filename, sheet, "Hay datos sin encabezado", code=code, row=line))
        for index, value in enumerate(cells[:len(columns)]):
            if isinstance(value, str) and value.startswith("="):
                errors.append(issue(filename, sheet, "Reemplaza la fórmula por su valor antes de cargar", code=code, row=line, field=columns[index], value=value))
        rows.append({**{key: cells[i] if i < len(cells) else None for i, key in enumerate(columns)}, "__source_row__": line, "__source_sheet__": sheet, "__source_file__": filename})
    if not rows: errors.append(issue(filename, sheet, "La plantilla no contiene filas de datos", code=code))
    if errors: return None, errors
    method = "nombre y columnas" if named or expected else "columnas únicas"
    return RecognizedObject(code, filename, sheet, rows, [issue(filename, sheet, f"Plantilla reconocida por {method}: {len(rows)} filas preparadas", code=code, severity="success")]), []


def recognize_file(filename, content, allowed, expected=None):
    filename = PurePath(filename.replace("\\", "/")).name
    if not content or len(content) > MAX_FILE_BYTES:
        reject([issue(filename, "", "El archivo debe contener datos y pesar hasta 20 MB")])
    if not filename.lower().endswith((".xlsx", ".csv")):
        reject([issue(filename, "", "El archivo no es una plantilla Excel XLSX o CSV")])
    found, errors = [], []
    book = None
    try:
        if filename.lower().endswith(".csv"):
            decoded = content.decode("utf-8-sig")
            try: dialect = csv.Sniffer().sniff(decoded[:8192], delimiters=",;\t")
            except csv.Error: dialect = csv.excel
            item, messages = _sheet(iter(csv.reader(io.StringIO(decoded), dialect)), filename, "Plantilla", allowed, expected)
            if item: found.append(item)
            errors.extend(messages)
        else:
            with ZipFile(io.BytesIO(content)) as archive:
                if len(archive.infolist()) > 500 or sum(item.file_size for item in archive.infolist()) > MAX_EXPANDED_BYTES:
                    raise ValueError("El Excel descomprimido supera 80 MB o 500 componentes")
            book = load_workbook(io.BytesIO(content), read_only=True, data_only=False)
            sheets = [sheet for sheet in book if normalized_name(sheet.title) not in SUPPORT_SHEETS]
            if not sheets or len(sheets) > len(DEFINITIONS): raise ValueError("El libro no contiene hojas de plantilla o supera el número de objetos admitidos")
            if expected and len(sheets) != 1: raise ValueError("El Excel contiene varias plantillas. Usa Carga masiva para asignarlas juntas")
            for sheet in sheets:
                if sheet.max_column and sheet.max_column > 100:
                    errors.append(issue(filename, sheet.title, "La hoja supera 100 columnas")); continue
                item, messages = _sheet(iter(sheet.iter_rows(values_only=True)), filename, sheet.title, allowed, expected, len(sheets) > 1)
                if item: found.append(item)
                errors.extend(messages)
    except (ValueError, BadZipFile, csv.Error, UnicodeDecodeError) as error:
        errors.append(issue(filename, "", str(error)))
    except Exception:
        errors.append(issue(filename, "", "No se pudo leer el archivo. Usa un XLSX válido, sin contraseña ni macros"))
    finally:
        if book: book.close()
    if errors: reject(errors)
    return found
