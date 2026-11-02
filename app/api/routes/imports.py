"""Safe, tenant-scoped spreadsheet migrations.

The API deliberately receives the file twice (preview and commit).  A preview is
never trusted as an authorization token: the commit reparses and revalidates the
same file inside one database transaction.
"""

from __future__ import annotations

import csv
from hashlib import sha256
import io
from pydantic import ValidationError
import re
import unicodedata
from zipfile import BadZipFile, ZipFile
from collections import defaultdict
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from openpyxl import load_workbook
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import (
    AccountPayable,
    AccountReceivable,
    Category,
    Customer,
    Product,
    ProductMaterialProfile,
    Purchase,
    PurchaseItem,
    Sale,
    SaleItem,
    Supplier,
    Tenant,
    UnitOfMeasure,
    Warehouse,
)
from app.security.auth import Actor, require_owner_or_admin, require_permission, require_second_factor
from app.schemas.materials import MaterialProfile
from app.schemas.catalog import CustomerCreate, SupplierCreate
from app.schemas.inventory import WarehouseCreate
from app.services.material_master import PROFILE_FIELDS, material_template
from app.services.material_capabilities import material_profiles_available, supported_material_template
from app.services.migration_workbooks import csv_bytes, workbook_bytes
from app.services.accounting import post_entry, split_tax
from app.services.audit import record
from app.services.idempotency import claim as claim_idempotency, complete as complete_idempotency, replay_response
from app.services.operational_context import resolve_warehouse_context
from app.services.stock import change_stock
from app.services.taxes import resolve_default_tax_rate
from app.services.cost_centers import resolve_cost_center

router = APIRouter(prefix="/imports", tags=["imports"])

MAX_FILE_SIZE = 5 * 1024 * 1024
MAX_ROWS = 2_000


def _ensure_import_permission(module: str, actor: Actor, db: Session) -> None:
    if module not in TEMPLATES:
        raise HTTPException(status_code=404, detail="No hay plantilla para este módulo")
    resource = "inventory" if module == "warehouses" else module
    require_permission(f"{resource}:write")(actor, db)


def _ensure_import_module_allowed(module: str, actor: Actor, db: Session) -> None:
    """Keep retail sales creation exclusive to the POS channel.

    Importing historical sales would otherwise bypass the same commercial
    channel rule enforced by the administrative sales endpoint.
    """
    if module != "sales":
        return
    tenant = db.get(Tenant, actor.tenant_id)
    if tenant and tenant.business_profile == "retail":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Las empresas minoristas registran ventas exclusivamente desde Punto de venta",
        )

TEMPLATES: dict[str, dict[str, Any]] = {
    "products": material_template(),
    "customers": {
        "label": "Clientes",
        "columns": ["code", "name", "tax_id", "email", "phone", "credit_limit"],
        "example": ["CLI-001", "Cliente de ejemplo", "12345678", "contacto@cliente.com", "999000000", "0"],
    },
    "suppliers": {
        "label": "Proveedores",
        "columns": ["code", "name", "tax_id", "email", "phone", "address"],
        "example": ["PRV-001", "Proveedor de ejemplo", "20123456789", "ventas@proveedor.com", "999000000", "Dirección fiscal"],
    },
    "warehouses": {
        "label": "Almacenes",
        "columns": ["code", "name", "address"],
        "example": ["ALM-001", "Almacén principal", "Dirección del almacén"],
    },
    "inventory": {
        "label": "Ajustes de inventario",
        "columns": ["number", "sku", "warehouse_code", "quantity", "reason", "reference", "note"],
        "example": ["AJ-20261019-001", "SKU-001", "ALM-001", "25", "Carga inicial", "MIG-001", "Existencia inicial"],
    },
    "sales": {
        "label": "Ventas",
        "columns": ["number", "customer_tax_id", "customer_name", "warehouse_code", "product_sku", "quantity", "unit_price", "due_date", "note"],
        "example": ["V-20261019-001", "12345678", "Cliente de ejemplo", "ALM-001", "SKU-001", "2", "100.00", "2026-10-19", "Migración histórica"],
    },
    "purchases": {
        "label": "Compras",
        "columns": ["number", "supplier_tax_id", "supplier_name", "warehouse_code", "product_sku", "quantity", "unit_cost", "due_date", "note"],
        "example": ["C-20261019-001", "20123456789", "Proveedor de ejemplo", "ALM-001", "SKU-001", "10", "80.00", "2026-10-19", "Migración histórica"],
    },
}


def _header(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _decimal(value: Any, field: str, *, whole: bool = False, positive: bool = False) -> Decimal:
    raw = _text(value).replace(" ", "").replace(",", ".")
    try:
        number = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} debe ser numérico") from exc
    limit = Decimal("100000000000000" if whole else "1000000000000")
    if not number.is_finite() or abs(number) >= limit:
        raise ValueError(f"{field} debe ser finito y menor que {limit} en valor absoluto")
    if whole and number != number.to_integral_value():
        raise ValueError(f"{field} debe ser un número entero")
    if positive and number <= 0:
        raise ValueError(f"{field} debe ser mayor que cero")
    result = number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if not whole else number
    if abs(result) >= limit:
        raise ValueError(f"{field} supera la precisión admitida")
    return result


def _date(value: Any, field: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(_text(value))
    except ValueError as exc:
        raise ValueError(f"{field} debe usar el formato AAAA-MM-DD") from exc


async def _rows_from_upload(upload: UploadFile) -> list[dict[str, Any]]:
    filename = (upload.filename or "").lower()
    if not filename.endswith((".csv", ".xlsx")):
        raise HTTPException(status_code=422, detail="Carga una plantilla CSV o XLSX")
    content = await upload.read(MAX_FILE_SIZE + 1)
    if not content or len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=422, detail="La plantilla debe contener datos y pesar como máximo 5 MB")
    book = None
    try:
        if filename.endswith(".csv"):
            decoded = content.decode("utf-8-sig")
            try:
                dialect = csv.Sniffer().sniff(decoded[:8192], delimiters=",;\t")
            except csv.Error:
                dialect = csv.excel
            values = iter(csv.reader(io.StringIO(decoded), dialect=dialect))
        else:
            # Bound ZIP expansion before openpyxl sees shared strings or XML.
            with ZipFile(io.BytesIO(content)) as archive:
                if len(archive.infolist()) > 200 or sum(item.file_size for item in archive.infolist()) > 30 * 1024 * 1024:
                    raise ValueError("El XLSX descomprimido supera el límite de 30 MB o 200 archivos")
            book = load_workbook(io.BytesIO(content), read_only=True, data_only=False)
            # Instructions/catalogs may be active when a user saves the workbook.
            sheet = book["Plantilla"] if "Plantilla" in book.sheetnames else book.active
            if sheet.max_column and sheet.max_column > 100:
                raise ValueError("La plantilla admite hasta 100 columnas")
            values = sheet.iter_rows(values_only=True)
        headers = next(values, None)
        if not headers or len(headers) > 100:
            raise ValueError("La plantilla necesita encabezados y hasta 100 columnas")
        normalized = [_header(header) for header in headers]
        named = [header for header in normalized if header]
        if not named or len(named) != len(set(named)):
            raise ValueError("Los encabezados no pueden estar vacíos ni repetidos")
        rows = []
        for source_row, values_row in enumerate(values, start=2):
            if source_row > MAX_ROWS + 1:
                raise ValueError(f"La plantilla admite hasta {MAX_ROWS} filas por importación")
            if not any(value not in (None, "") for value in values_row):
                continue
            if any(value not in (None, "") for value in values_row[len(normalized):]):
                raise ValueError(f"Fila {source_row}: hay valores sin encabezado")
            if any(not normalized[index] and value not in (None, "") for index, value in enumerate(values_row[:len(normalized)])):
                raise ValueError(f"Fila {source_row}: hay valores sin encabezado")
            if book and any(isinstance(value, str) and value.startswith("=") for value in values_row):
                raise ValueError(f"Fila {source_row}: reemplaza las fórmulas por valores antes de cargar")
            row = {key: values_row[index] if index < len(values_row) else None for index, key in enumerate(normalized) if key}
            row["__source_row__"] = source_row
            rows.append(row)
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="El CSV debe estar codificado en UTF-8") from exc
    except (ValueError, csv.Error, BadZipFile) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail="No se pudo leer la plantilla CSV o XLSX") from exc
    finally:
        if book:
            book.close()
    if not rows:
        raise HTTPException(status_code=422, detail="La plantilla no contiene filas de datos")
    if len(rows) > MAX_ROWS:
        raise HTTPException(status_code=422, detail=f"La plantilla admite hasta {MAX_ROWS} filas por importación")
    return rows


def _required(module: str, rows: list[dict[str, Any]]) -> list[str]:
    columns = TEMPLATES[module].get("required_columns", TEMPLATES[module]["columns"])
    present = set(rows[0])
    return [column for column in columns if column not in present]


def _validate_master(module: str, row: dict[str, Any]) -> dict[str, Any]:
    if module == "products":
        sku, name = _text(row.get("sku")), _text(row.get("name"))
        if not sku or not name:
            raise ValueError("sku y name son obligatorios")
        category, unit = _text(row.get("category")), _text(row.get("unit")) or "unit"
        if len(sku) > 64 or len(name) > 160 or len(category) > 100 or len(unit) > 80:
            raise ValueError("Longitud máxima: sku 64, name 160, category 100 y unit 80")
        price = _decimal(row.get("sale_price") or 0, "sale_price")
        cost = _decimal(row.get("average_cost") or 0, "average_cost")
        if price < 0 or cost < 0:
            raise ValueError("sale_price y average_cost no pueden ser negativos")
        tracking = _text(row.get("tracking_mode")) or "none"
        method = _text(row.get("cost_method")) or "weighted_average"
        if tracking not in {"none", "lot", "serial"}:
            raise ValueError("tracking_mode debe ser none, lot o serial")
        if method not in {"weighted_average", "fifo"}:
            raise ValueError("cost_method debe ser weighted_average o fifo")
        active = _text(row.get("is_active")).lower()
        if active not in {"", "true", "false", "1", "0", "si", "sí", "no"}:
            raise ValueError("is_active debe ser true o false")
        attributes = {key: row[key] for key in PROFILE_FIELDS if row.get(key) not in (None, "")}
        profile = MaterialProfile.model_validate(attributes).model_dump(mode="json", exclude_unset=True) if attributes else {}
        return {"sku": sku, "name": name, "category": category, "unit": unit, "sale_price": price, "average_cost": cost, "tracking_mode": tracking, "cost_method": method, "is_active": active not in {"false", "0", "no"}, "material_profile": profile}
    if module == "customers":
        code, name = _text(row.get("code")), _text(row.get("name"))
        tax_id = _text(row.get("tax_id"))
        if not code or not name:
            raise ValueError("code y name son obligatorios")
        if tax_id and (not tax_id.isdigit() or len(tax_id) not in {8, 11}):
            raise ValueError("tax_id debe ser DNI de 8 o RUC de 11 dígitos")
        return CustomerCreate(code=code, name=name, tax_id=tax_id or None, email=_text(row.get("email")) or None, phone=_text(row.get("phone")) or None, credit_limit=_decimal(row.get("credit_limit") or 0, "credit_limit")).model_dump()
    if module == "suppliers":
        code, name, tax_id = _text(row.get("code")), _text(row.get("name")), _text(row.get("tax_id"))
        if not code or not name:
            raise ValueError("code y name son obligatorios")
        if tax_id and (not tax_id.isdigit() or len(tax_id) != 11):
            raise ValueError("tax_id debe ser un RUC de 11 dígitos")
        return SupplierCreate(code=code, name=name, tax_id=tax_id or None, email=_text(row.get("email")) or None, phone=_text(row.get("phone")) or None, address=_text(row.get("address")) or None).model_dump()
    if module == "warehouses":
        code, name = _text(row.get("code")), _text(row.get("name"))
        if not code or not name:
            raise ValueError("code y name son obligatorios")
        return WarehouseCreate(code=code, name=name, address=_text(row.get("address")) or None).model_dump(exclude_unset=True)
    raise ValueError("Módulo de migración no reconocido")


def _validate_transaction(module: str, row: dict[str, Any]) -> dict[str, Any]:
    quantity = _decimal(row.get("quantity"), "quantity", whole=True, positive=module != "inventory")
    if module == "inventory" and quantity == 0:
        raise ValueError("quantity no puede ser cero")
    # Inventory adjustments use the concise `sku` header, while sales and
    # purchases retain `product_sku` for clarity in multi-document sheets.
    product_sku = _text(row.get("sku" if module == "inventory" else "product_sku"))
    common = {"number": _text(row.get("number")), "warehouse_code": _text(row.get("warehouse_code")), "product_sku": product_sku, "quantity": quantity, "due_date": _date(row.get("due_date"), "due_date"), "note": _text(row.get("note")) or None}
    if not common["number"] or not common["warehouse_code"] or not common["product_sku"]:
        raise ValueError("number, warehouse_code y product_sku son obligatorios")
    if module == "inventory":
        reason = _text(row.get("reason"))
        if not reason:
            raise ValueError("reason es obligatorio")
        return {**common, "reason": reason, "reference": _text(row.get("reference")) or common["number"]}
    if module == "sales":
        tax_id, name = _text(row.get("customer_tax_id")), _text(row.get("customer_name"))
        if not tax_id.isdigit() or len(tax_id) not in {8, 11} or not name:
            raise ValueError("customer_tax_id (DNI/RUC) y customer_name son obligatorios")
        value = _decimal(row.get("unit_price"), "unit_price")
        if value < 0:
            raise ValueError("unit_price no puede ser negativo")
        return {**common, "counterpart_tax_id": tax_id, "counterpart_name": name, "unit_value": value}
    if module == "purchases":
        tax_id, name = _text(row.get("supplier_tax_id")), _text(row.get("supplier_name"))
        if not tax_id.isdigit() or len(tax_id) != 11 or not name:
            raise ValueError("supplier_tax_id (RUC) y supplier_name son obligatorios")
        value = _decimal(row.get("unit_cost"), "unit_cost")
        if value < 0:
            raise ValueError("unit_cost no puede ser negativo")
        return {**common, "counterpart_tax_id": tax_id, "counterpart_name": name, "unit_value": value}
    raise ValueError("Módulo de migración no reconocido")


def _validate_rows(module: str, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    missing = _required(module, rows)
    if missing:
        return [], [{"row": 1, "message": f"Faltan columnas: {', '.join(missing)}"}]
    valid, errors = [], []
    if module == "products":
        unknown = set().union(*(set(row) for row in rows)) - set(TEMPLATES[module]["columns"]) - {"__source_row__"}
        if unknown:
            return [], [{"row": 1, "message": f"Columnas no reconocidas: {', '.join(sorted(unknown))}"}]
    validator: Callable[[str, dict[str, Any]], dict[str, Any]] = _validate_master if module in {"products", "customers", "suppliers", "warehouses"} else _validate_transaction
    seen: set[str] = set()
    for index, row in enumerate(rows, start=2):
        source_row = row.get("__source_row__", index)
        try:
            validated = validator(module, row)
            if module in {"products", "customers", "suppliers", "warehouses"}:
                key = "sku" if module == "products" else "code"
                value = validated[key].casefold()
                if value in seen:
                    raise ValueError(f"{key} está repetido dentro de la plantilla")
                seen.add(value)
            valid.append(validated)
        except ValidationError as exc:
            errors.extend({"row": source_row, "field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]} for item in exc.errors())
        except ValueError as exc:
            errors.append({"row": source_row, "message": str(exc)})
    return valid, errors


def _check_database_conflicts(module: str, rows: list[dict[str, Any]], actor: Actor, db: Session) -> list[str]:
    if module == "products":
        existing = set(db.scalars(select(Product.sku).where(Product.tenant_id == actor.tenant_id, func.lower(Product.sku).in_([row["sku"].lower() for row in rows]))).all())
        return [f"SKU ya existe: {value}" for value in existing]
    if module == "customers":
        existing = set(db.scalars(select(Customer.code).where(Customer.tenant_id == actor.tenant_id, Customer.code.in_([row["code"] for row in rows]))).all())
        return [f"Código de cliente ya existe: {value}" for value in existing]
    if module == "suppliers":
        existing = set(db.scalars(select(Supplier.code).where(Supplier.tenant_id == actor.tenant_id, Supplier.code.in_([row["code"] for row in rows]))).all())
        return [f"Código de proveedor ya existe: {value}" for value in existing]
    if module == "warehouses":
        existing = set(db.scalars(select(Warehouse.code).where(Warehouse.tenant_id == actor.tenant_id, Warehouse.code.in_([row["code"] for row in rows]))).all())
        return [f"Código de almacén ya existe: {value}" for value in existing]
    if module == "sales":
        numbers = {row["number"] for row in rows}
        existing = set(db.scalars(select(Sale.number).where(Sale.tenant_id == actor.tenant_id, Sale.number.in_(numbers))).all())
        return [f"Venta ya existe: {value}" for value in existing]
    if module == "purchases":
        numbers = {row["number"] for row in rows}
        existing = set(db.scalars(select(Purchase.number).where(Purchase.tenant_id == actor.tenant_id, Purchase.number.in_(numbers))).all())
        return [f"Compra ya existe: {value}" for value in existing]
    return []


def _resolve_import_unit(value: str, units: list[UnitOfMeasure]) -> UnitOfMeasure:
    code = value.strip().casefold()
    if code in {"unit", "unidad", "un"}:
        code = "niu"
    matches = [unit for unit in units if unit.code.casefold() == code]
    if not matches:
        matches = [unit for unit in units if unit.name.casefold() == code]
    if len(matches) != 1:
        raise ValueError(f"Unidad no encontrada, inactiva o ambigua: {value}. Usa un código activo de Configuración.")
    return matches[0]


def _product_references(rows: list[dict[str, Any]], actor: Actor, db: Session) -> list[str]:
    units = list(db.scalars(select(UnitOfMeasure).where(UnitOfMeasure.tenant_id == actor.tenant_id, UnitOfMeasure.is_active.is_(True))).all())
    errors = []
    for value in dict.fromkeys(row["unit"] for row in rows):
        try:
            _resolve_import_unit(value, units)
        except ValueError as exc:
            errors.append(str(exc))
    return errors


def _reference_maps(actor: Actor, db: Session) -> tuple[dict[str, Product], dict[str, Warehouse]]:
    products = {item.sku: item for item in db.scalars(select(Product).where(Product.tenant_id == actor.tenant_id, Product.is_active.is_(True))).all()}
    warehouses = {item.code: item for item in db.scalars(select(Warehouse).where(Warehouse.tenant_id == actor.tenant_id, Warehouse.is_active.is_(True))).all()}
    return products, warehouses


def _transaction_references(module: str, rows: list[dict[str, Any]], actor: Actor, db: Session) -> list[str]:
    products, warehouses = _reference_maps(actor, db)
    errors: list[str] = []
    for row in rows:
        if row["product_sku"] not in products:
            errors.append(f"Producto no encontrado o inactivo: {row['product_sku']}")
        if row["warehouse_code"] not in warehouses:
            errors.append(f"Almacén no encontrado o inactivo: {row['warehouse_code']}")
    return list(dict.fromkeys(errors))


def _persist_master(module: str, rows: list[dict[str, Any]], actor: Actor, db: Session) -> int:
    units = list(db.scalars(select(UnitOfMeasure).where(UnitOfMeasure.tenant_id == actor.tenant_id, UnitOfMeasure.is_active.is_(True))).all()) if module == "products" else []
    for row in rows:
        if module == "products":
            category = None
            if row["category"]:
                category = db.scalar(select(Category).where(Category.tenant_id == actor.tenant_id, Category.name == row["category"]))
                if category is None:
                    category = Category(tenant_id=actor.tenant_id, name=row["category"])
                    db.add(category); db.flush()
            try:
                unit = _resolve_import_unit(row["unit"], units)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            entity = Product(tenant_id=actor.tenant_id, sku=row["sku"], name=row["name"], category_id=category.id if category else None, unit_id=unit.id, unit=unit.code, unit_code=unit.code, unit_name=unit.name, sale_price=row["sale_price"], average_cost=row["average_cost"], tracking_mode=row["tracking_mode"], cost_method=row["cost_method"], is_active=row["is_active"])
        elif module == "customers":
            entity = Customer(tenant_id=actor.tenant_id, **row)
        elif module == "suppliers":
            entity = Supplier(tenant_id=actor.tenant_id, **row)
        else:
            entity = Warehouse(tenant_id=actor.tenant_id, **row)
        db.add(entity); db.flush()
        if module == "products" and row.get("material_profile"):
            db.add(ProductMaterialProfile(tenant_id=actor.tenant_id, product_id=entity.id, attributes=row["material_profile"]))
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="import", module=module, entity=module.rstrip("s"), entity_id=entity.id)
    return len(rows)


def _get_or_create_counterpart(module: str, row: dict[str, Any], actor: Actor, db: Session) -> Customer | Supplier:
    if module == "sales":
        entity = db.scalar(select(Customer).where(Customer.tenant_id == actor.tenant_id, Customer.tax_id == row["counterpart_tax_id"]))
        if entity is None:
            entity = Customer(tenant_id=actor.tenant_id, code=f"CLI-{row['counterpart_tax_id']}", name=row["counterpart_name"], tax_id=row["counterpart_tax_id"])
            db.add(entity); db.flush()
        return entity
    entity = db.scalar(select(Supplier).where(Supplier.tenant_id == actor.tenant_id, Supplier.tax_id == row["counterpart_tax_id"]))
    if entity is None:
        entity = Supplier(tenant_id=actor.tenant_id, code=f"PRV-{row['counterpart_tax_id']}", name=row["counterpart_name"], tax_id=row["counterpart_tax_id"])
        db.add(entity); db.flush()
    return entity


def _persist_transactions(module: str, rows: list[dict[str, Any]], actor: Actor, db: Session) -> int:
    products, warehouses = _reference_maps(actor, db)
    if module == "inventory":
        for row in rows:
            product, warehouse = products[row["product_sku"]], warehouses[row["warehouse_code"]]
            change_stock(db, tenant_id=actor.tenant_id, product_id=product.id, warehouse_id=warehouse.id, quantity=row["quantity"], movement_type="adjustment", reference=row["reference"], note=row["note"] or row["reason"])
            record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="import_adjustment", module="inventory", entity="inventory_movement", detail=row["reason"])
        return len(rows)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["number"]].append(row)
    for number, lines in grouped.items():
        first, warehouse = lines[0], warehouses[lines[0]["warehouse_code"]]
        if any(line["warehouse_code"] != first["warehouse_code"] for line in lines):
            raise HTTPException(status_code=422, detail=f"El documento {number} mezcla almacenes")
        if len({line["product_sku"] for line in lines}) != len(lines):
            raise HTTPException(status_code=422, detail=f"El documento {number} repite un SKU")
        counterpart = _get_or_create_counterpart(module, first, actor, db)
        total = sum((line["quantity"] * line["unit_value"] for line in lines), Decimal("0"))
        tax_rate = resolve_default_tax_rate(
            db, tenant_id=actor.tenant_id, document_date=date.today(),
        )
        taxable_amount, tax_amount = split_tax(total, tax_rate.rate)
        cost_center = resolve_cost_center(
            db, tenant_id=actor.tenant_id, cost_center_id=None,
        )
        operational_context = resolve_warehouse_context(
            db, tenant_id=actor.tenant_id, warehouse_id=warehouse.id,
        )
        if module == "sales":
            sale = Sale(
                tenant_id=actor.tenant_id, number=number, customer_id=counterpart.id,
                warehouse_id=warehouse.id,
                legal_entity_id=operational_context.legal_entity.id if operational_context.legal_entity else None,
                branch_id=operational_context.branch.id if operational_context.branch else None,
                status="posted", total=total, note=first["note"],
                cost_center_id=cost_center.id, cost_center_code=cost_center.code,
                cost_center_name=cost_center.name,
                tax_rate_id=tax_rate.id, tax_rate_code=tax_rate.code,
                tax_rate_name=tax_rate.name, tax_rate_percent=tax_rate.rate,
                taxable_amount=taxable_amount, tax_amount=tax_amount,
            )
            db.add(sale); db.flush()
            db.add(AccountReceivable(tenant_id=actor.tenant_id, sale_id=sale.id, customer_id=counterpart.id, original_amount=total, balance_amount=total, status="open", due_date=first["due_date"] or date.today()))
            for line in lines:
                product = products[line["product_sku"]]
                db.add(SaleItem(tenant_id=actor.tenant_id, sale_id=sale.id, product_id=product.id, quantity=line["quantity"], unit_price=line["unit_value"], line_total=line["quantity"] * line["unit_value"]))
                change_stock(db, tenant_id=actor.tenant_id, product_id=product.id, warehouse_id=warehouse.id, quantity=-line["quantity"], movement_type="issue", reference=number)
            post_entry(db, tenant_id=actor.tenant_id, actor_id=actor.user_id, number=f"AS-{number}", description=f"Venta importada {number}", source_type="sale", source_id=sale.id, lines=[("1210", total, Decimal("0"), cost_center.id), ("7010", Decimal("0"), taxable_amount, cost_center.id), ("4010", Decimal("0"), tax_amount, cost_center.id)])
            record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="import", module="sales", entity="sale", entity_id=sale.id)
        else:
            purchase = Purchase(
                tenant_id=actor.tenant_id, number=number, supplier_id=counterpart.id,
                warehouse_id=warehouse.id,
                legal_entity_id=operational_context.legal_entity.id if operational_context.legal_entity else None,
                branch_id=operational_context.branch.id if operational_context.branch else None,
                status="received", total=total, note=first["note"],
                cost_center_id=cost_center.id, cost_center_code=cost_center.code,
                cost_center_name=cost_center.name,
                tax_rate_id=tax_rate.id, tax_rate_code=tax_rate.code,
                tax_rate_name=tax_rate.name, tax_rate_percent=tax_rate.rate,
                taxable_amount=taxable_amount, tax_amount=tax_amount,
            )
            db.add(purchase); db.flush()
            db.add(AccountPayable(tenant_id=actor.tenant_id, purchase_id=purchase.id, supplier_id=counterpart.id, original_amount=total, balance_amount=total, status="open", due_date=first["due_date"] or date.today()))
            for line in lines:
                product = products[line["product_sku"]]
                db.add(PurchaseItem(tenant_id=actor.tenant_id, purchase_id=purchase.id, product_id=product.id, quantity=line["quantity"], unit_cost=line["unit_value"], line_total=line["quantity"] * line["unit_value"]))
                change_stock(db, tenant_id=actor.tenant_id, product_id=product.id, warehouse_id=warehouse.id, quantity=line["quantity"], movement_type="receipt", reference=number)
            post_entry(db, tenant_id=actor.tenant_id, actor_id=actor.user_id, number=f"AS-{number}", description=f"Compra importada {number}", source_type="purchase", source_id=purchase.id, lines=[("2010", taxable_amount, Decimal("0"), cost_center.id), ("4011", tax_amount, Decimal("0"), cost_center.id), ("4210", Decimal("0"), total, cost_center.id)])
            record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="import", module="purchases", entity="purchase", entity_id=purchase.id)
    return len(grouped)


async def _preview(module: str, upload: UploadFile, actor: Actor, db: Session) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    if module not in TEMPLATES:
        raise HTTPException(status_code=404, detail="No hay plantilla para este módulo")
    rows = await _rows_from_upload(upload)
    valid, errors = _validate_rows(module, rows)
    conflicts = _check_database_conflicts(module, valid, actor, db) if not errors else []
    references = _transaction_references(module, valid, actor, db) if module in {"inventory", "sales", "purchases"} and not errors else []
    if module == "products" and not errors:
        references += _product_references(valid, actor, db)
        if any(row.get("material_profile") for row in valid) and not material_profiles_available(db):
            references.append("La ficha industrial aún no está habilitada. Usa la plantilla disponible o solicita su habilitación al administrador.")
    return valid, errors, conflicts + references


@router.get("/templates")
def list_templates(actor: Actor = Depends(require_owner_or_admin), db: Session = Depends(get_db)):
    result = []
    for module, template in TEMPLATES.items():
        try:
            _ensure_import_permission(module, actor, db)
            _ensure_import_module_allowed(module, actor, db)
        except HTTPException as error:
            if error.status_code == 403: continue
            raise
        result.append({"module": module, **(supported_material_template(template, db) if module == "products" else template), "download_url": f"/imports/{module}/template", "export_url": "/imports/products/export" if module == "products" else None})
    return result


@router.get("/{module}/template")
def download_template(module: str, format: str = Query(default="xlsx", pattern="^(csv|xlsx)$"), actor: Actor = Depends(require_owner_or_admin), db: Session = Depends(get_db)):
    _ensure_import_permission(module, actor, db)
    _ensure_import_module_allowed(module, actor, db)
    template = supported_material_template(TEMPLATES[module], db) if module == "products" else TEMPLATES[module]
    catalogs = []
    if module == "products":
        catalogs += [["Unidad", item.code, item.name] for item in db.scalars(select(UnitOfMeasure).where(UnitOfMeasure.tenant_id == actor.tenant_id, UnitOfMeasure.is_active.is_(True))).all()]
        catalogs += [["Categoría", item.name, item.description or ""] for item in db.scalars(select(Category).where(Category.tenant_id == actor.tenant_id)).all()]
    content = csv_bytes(template["columns"], []) if format == "csv" else workbook_bytes(template, catalogs=catalogs)
    mime = "text/csv; charset=utf-8" if format == "csv" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(content=content, media_type=mime, headers={"Content-Disposition": f'attachment; filename="plantilla-{module}.{format}"', "Cache-Control": "no-store"})


@router.get("/products/export")
def export_materials(format: str = Query(default="xlsx", pattern="^(csv|xlsx)$"), actor: Actor = Depends(require_owner_or_admin), db: Session = Depends(get_db)):
    _ensure_import_permission("products", actor, db)
    # Fetch one beyond the bound and refuse rather than silently truncating.
    products = db.scalars(select(Product).where(Product.tenant_id == actor.tenant_id).order_by(Product.sku, Product.id).limit(10001)).all()
    if len(products) > 10000:
        raise HTTPException(status_code=422, detail="La exportación interactiva admite hasta 10000 materiales. Usa un reporte segmentado para volúmenes mayores.")
    template = supported_material_template(TEMPLATES["products"], db)
    profiles = {item.product_id: item.attributes for item in db.scalars(select(ProductMaterialProfile).where(ProductMaterialProfile.tenant_id == actor.tenant_id)).all()} if len(template["columns"]) > 9 else {}
    categories = {item.id: item.name for item in db.scalars(select(Category).where(Category.tenant_id == actor.tenant_id)).all()}
    rows = []
    for product in products:
        values = {"sku": product.sku, "name": product.name, "category": categories.get(product.category_id, ""), "unit": product.unit_code, "sale_price": str(product.sale_price), "average_cost": str(product.average_cost), "tracking_mode": product.tracking_mode, "cost_method": product.cost_method, "is_active": str(product.is_active).lower(), **profiles.get(product.id, {})}
        rows.append([values.get(key, "") for key in template["columns"]])
    content = csv_bytes(template["columns"], rows) if format == "csv" else workbook_bytes(template, rows=rows)
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="export", module="imports", entity="products", detail=f"{len(rows)} materiales; formato={format}")
    db.commit()
    mime = "text/csv; charset=utf-8" if format == "csv" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(content=content, media_type=mime, headers={"Content-Disposition": f'attachment; filename="materiales.{format}"', "Cache-Control": "no-store"})


@router.post("/{module}/preview")
async def preview_import(module: str, file: UploadFile = File(...), actor: Actor = Depends(require_owner_or_admin), db: Session = Depends(get_db)):
    _ensure_import_permission(module, actor, db)
    _ensure_import_module_allowed(module, actor, db)
    content = await file.read(MAX_FILE_SIZE + 1)
    await file.seek(0)
    valid, errors, conflicts = await _preview(module, file, actor, db)
    return {"module": module, "valid_rows": len(valid), "errors": errors, "conflicts": conflicts, "can_commit": not errors and not conflicts, "sample": valid[:5], "file_sha256": sha256(content).hexdigest(), "template_version": TEMPLATES[module].get("version", "1.0")}


@router.post("/{module}/commit", status_code=status.HTTP_201_CREATED)
async def commit_import(
    module: str,
    request: Request,
    file: UploadFile = File(...),
    preview_digest: str | None = Form(default=None, pattern=r"^[a-f0-9]{64}$"),
    actor: Actor = Depends(require_owner_or_admin),
    _: Actor = Depends(require_second_factor),
    db: Session = Depends(get_db),
):
    _ensure_import_permission(module, actor, db)
    _ensure_import_module_allowed(module, actor, db)
    # UploadFile is a stream.  Its digest is part of the request identity, so a
    # reused key cannot accidentally apply a different spreadsheet.  Rewind it
    # before validation because _preview deliberately reparses the source file.
    content = await file.read(MAX_FILE_SIZE + 1)
    await file.seek(0)
    # Invalid oversized files never reach a business mutation or the database.
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=422, detail="La plantilla debe contener datos y pesar como máximo 5 MB")
    digest = sha256(content).hexdigest()
    if isinstance(preview_digest, str) and preview_digest != digest:
        raise HTTPException(status_code=409, detail="El archivo cambió después de la validación. Vuelve a validarlo.")
    idempotency = claim_idempotency(
        db,
        tenant_id=actor.tenant_id,
        actor_user_id=actor.user_id,
        operation=f"imports.{module}.commit",
        key=request.headers.get("Idempotency-Key"),
        payload={"module": module, "filename": file.filename or "", "content_sha256": digest},
    )
    if idempotency and idempotency.is_replay:
        return replay_response(idempotency)
    valid, errors, conflicts = await _preview(module, file, actor, db)
    if errors or conflicts:
        raise HTTPException(status_code=422, detail={"message": "La plantilla contiene errores y no se migró ningún dato", "errors": errors, "conflicts": conflicts})
    try:
        count = _persist_master(module, valid, actor, db) if module in {"products", "customers", "suppliers", "warehouses"} else _persist_transactions(module, valid, actor, db)
        result = {"module": module, "imported": count, "file_sha256": digest}
        record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="bulk_import", module="imports", entity=module, detail=f"{count} registros; SHA256={digest}; plantilla={TEMPLATES[module].get('version', '1.0')}")
        complete_idempotency(
            db,
            claim=idempotency,
            response_body=result,
            response_status=status.HTTP_201_CREATED,
        )
        db.commit()
    except HTTPException:
        db.rollback(); raise
    except IntegrityError as exc:
        db.rollback(); raise HTTPException(status_code=409, detail="La importación entró en conflicto con datos ya existentes. No se guardó ningún registro.") from exc
    except Exception:
        db.rollback(); raise
    return result
