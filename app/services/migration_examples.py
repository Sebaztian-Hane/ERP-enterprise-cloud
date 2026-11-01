"""Coherent fictional examples. One project prefix links every workbook."""

from hashlib import sha256
from app.services.enterprise_catalog import RESOURCES
from app.services.migration_catalog import column_name


def example_rows(prefix="DEMO"):
    identifiers = {resource.code: f"{prefix}-{index + 1:02}" for index, resource in enumerate(RESOURCES)}
    identifiers.update(products=f"{prefix}-PT", customers=f"{prefix}-C", suppliers=f"{prefix}-P", warehouses=f"{prefix}-A", cost_centers=f"{prefix}-CC")
    unit, component = f"{prefix}-U", f"{prefix}-MP"
    numeric = int(sha256(prefix.encode()).hexdigest()[:10], 16)
    dni, ruc = f"{numeric % 100000000:08}", f"20{numeric % 1000000000:09}"
    material = {"category": "Pruebas de migración", "unit": unit, "sale_price": 118, "average_cost": 80, "tracking_mode": "none", "cost_method": "weighted_average", "is_active": "true", "description": "Material ficticio para evaluación", "material_type": "finished", "brand": "DEMO", "net_weight": 1, "gross_weight": 1.1, "weight_unit": "KG", "country_of_origin": "PE", "lead_time_days": 7, "minimum_order_quantity": 1, "reorder_point": 5, "safety_stock": 3, "storage_conditions": "Lugar seco"}
    result = {
        "units": [{"code": unit, "name": "Unidad de prueba"}],
        "cost_centers": [{"code": identifiers["cost_centers"], "name": "Centro de evaluación"}],
        "products": [{**material, "sku": identifiers["products"], "name": "Conjunto de evaluación", "barcode": f"000{numeric % 1000000000:09}"}, {**material, "sku": component, "name": "Componente de prueba", "material_type": "raw_material", "sale_price": 35.4, "average_cost": 20}],
        "customers": [{"code": identifiers["customers"], "name": "Cliente ficticio de evaluación", "tax_id": dni, "email": "cliente@example.com", "phone": "", "credit_limit": 1000}],
        "suppliers": [{"code": identifiers["suppliers"], "name": "Proveedor ficticio de evaluación", "tax_id": ruc, "email": "proveedor@example.com", "phone": "", "address": "Dirección de prueba"}],
        "warehouses": [{"code": identifiers["warehouses"], "name": "Almacén de evaluación", "address": "Planta de prueba"}],
        "inventory": [{"number": f"{prefix}-AJ-{index}", "sku": sku, "warehouse_code": identifiers["warehouses"], "quantity": quantity, "reason": "Existencia inicial de prueba", "reference": f"{prefix}-INI-{index}", "note": "Carga ficticia"} for index, (sku, quantity) in enumerate(((identifiers["products"], 20), (component, 100)), 1)],
        "purchases": [{"number": f"{prefix}-COM", "supplier_tax_id": ruc, "supplier_name": "Proveedor ficticio de evaluación", "warehouse_code": identifiers["warehouses"], "product_sku": identifiers["products"], "quantity": 5, "unit_cost": 94.4, "due_date": "2027-01-15", "note": "Compra ficticia; crea recepción y obligación"}],
        "sales": [{"number": f"{prefix}-VEN", "customer_tax_id": dni, "customer_name": "Cliente ficticio de evaluación", "warehouse_code": identifiers["warehouses"], "product_sku": identifiers["products"], "quantity": 2, "unit_price": 118, "due_date": "2027-01-15", "note": "Venta ficticia; crea salida y obligación"}],
    }
    for resource in RESOURCES:
        row = {"number": identifiers[resource.code], "title": f"Prueba · {resource.label}"}
        for field in resource.fields:
            column = column_name(field)
            if field.kind == "lines": row.update(component_sku=component, component_quantity=2)
            elif not field.required: row[column] = ""
            elif field.reference: row[column] = identifiers[field.reference.removeprefix("core:")]
            elif field.choices: row[column] = field.choices[0]
            elif field.kind == "date": row[column] = "2027-01-01"
            elif field.kind in {"number", "integer"}: row[column] = max(field.minimum or 0, 1)
            else: row[column] = f"Prueba {field.label.lower()}"
        if "end_date" in row: row["end_date"] = "2027-12-31"
        if "planned_end" in row: row["planned_end"] = "2027-01-03"
        if resource.code == "employees": row.update(department="Producción", position="Técnico de prueba", hire_date="2026-01-01", work_email="empleado@example.com")
        if resource.code == "attendance": row["hours"] = 8
        if resource.code == "vehicles": row.update(license_plate=f"{prefix}-01", capacity_kg=1000)
        if resource.code == "freight_orders": row.update(weight_kg=500, origin="Lima", destination="Callao")
        if resource.code == "warehouse_tasks": row.update(source_bin="A-01", destination_bin="B-01", quantity=2)
        if resource.code == "projects": row["budget"] = 1000
        if resource.code == "project_tasks": row["progress"] = 0
        if resource.code == "supplier_quotes": row.update(unit_price=94.4, valid_until="2099-12-31", lead_days=7)
        if resource.code == "bills_of_material": row["base_quantity"] = 1
        if resource.code == "production_orders": row["quantity"] = 10
        result[resource.code] = [row]
    return result
