"""Migration objects use public business codes, never database UUIDs in Excel."""

from copy import deepcopy
from fastapi import HTTPException
from app.api.routes.imports import TEMPLATES
from app.services.enterprise_catalog import AREAS, RESOURCES, RESOURCE_BY_CODE
from app.services.enterprise import available as enterprise_available
from app.services.material_capabilities import supported_material_template
from app.services.workspace_access import workspace_access


CORE_MODULES = {"units": "products", "products": "products", "customers": "customers", "suppliers": "suppliers", "warehouses": "inventory", "inventory": "inventory", "sales": "sales", "purchases": "purchases", "cost_centers": "operations"}
DEPENDENCIES = {"products": ["units"], "inventory": ["products", "warehouses"], "sales": ["customers", "products", "warehouses", "inventory"], "purchases": ["suppliers", "products", "warehouses"]}
REFERENCE_OBJECTS = {"products": "products", "customers": "customers", "suppliers": "suppliers", "warehouses": "warehouses", "cost_centers": "cost_centers"}
EXTRA = {
    "units": {"label": "Unidades de medida", "columns": ["code", "name"], "example": ["DEMO-UN", "Unidad de prueba"]},
    "cost_centers": {"label": "Centros de costo", "columns": ["code", "name"], "example": ["DEMO-CC", "Centro de prueba"]},
}


def column_name(field):
    if field.reference == "core:products": return field.key.removesuffix("_id") + "_sku"
    return field.key.removesuffix("_id") + "_code" if field.reference else field.key


def definitions():
    result = {}
    for code, template in {**EXTRA, **TEMPLATES}.items():
        result[code] = {**deepcopy(template), "code": code, "module": CORE_MODULES[code], "dependencies": DEPENDENCIES.get(code, []), "version": "3.0", "effect": "Crea registros maestros" if code not in {"inventory", "sales", "purchases"} else "Registra movimientos de stock" if code == "inventory" else "Registra stock, obligación y asiento contable"}
        result[code]["required_dependencies"] = result[code]["dependencies"]
        if "fields" not in result[code]:
            optional = {"email", "phone", "address", "credit_limit", "due_date", "note", "tax_id", "reference"}
            result[code]["fields"] = [{"key": key, "group": template["label"], "required": key not in optional, "description": "Código o identidad de referencia; conserva los ceros iniciales como texto." if key.endswith(("_code", "_sku", "_tax_id")) or key in {"code", "tax_id", "sku"} else "Fecha AAAA-MM-DD." if key == "due_date" else "Consulta el valor de ejemplo. No cambies este encabezado."} for key in template["columns"]]
    for resource in RESOURCES:
        fields = [{"key": "number", "group": "Identificación", "required": True, "description": "Código único del documento dentro de la empresa."}, {"key": "title", "group": "Identificación", "required": True, "description": "Nombre o descripción."}]
        columns = ["number", "title"]
        dependencies = []
        required_dependencies = []
        for field in resource.fields:
            if field.kind == "lines":
                columns += ["component_sku", "component_quantity"]
                fields += [{"key": "component_sku", "group": "Componentes", "required": True, "description": "SKU del componente. Repite número, título y datos de cabecera en cada línea."}, {"key": "component_quantity", "group": "Componentes", "required": True, "description": "Cantidad positiva de componente por cantidad base."}]
                if "products" not in required_dependencies: required_dependencies.append("products")
                continue
            column = column_name(field)
            columns.append(column)
            target = field.reference
            description = f"Código de {target.split(':')[-1]} de esta empresa o de otra plantilla del grupo." if target else f"{field.label}. " + ("Valores: " + ", ".join(field.choices) if field.choices else "Fecha AAAA-MM-DD." if field.kind == "date" else "Número sin separador de miles." if field.kind in {"number", "integer"} else "Texto.")
            fields.append({"key": column, "group": resource.label, "required": field.required, "description": description})
            dependency = REFERENCE_OBJECTS.get(target.removeprefix("core:")) if target and target.startswith("core:") else target
            if dependency and dependency not in dependencies: dependencies.append(dependency)
            if dependency and field.required and dependency not in required_dependencies: required_dependencies.append(dependency)
        result[resource.code] = {"code": resource.code, "module": resource.module, "label": resource.label, "columns": columns, "example": [""] * len(columns), "fields": fields, "dependencies": dependencies, "version": "3.0", "effect": f"Crea documentos en estado inicial ({resource.initial}); conserva sus aprobaciones operativas. {resource.limitation}"}
        result[resource.code]["required_dependencies"] = required_dependencies
    return result


DEFINITIONS = definitions()


def accessible(actor, db):
    access = workspace_access(actor, db)
    modules = access["modules"]
    result = {code: item for code, item in DEFINITIONS.items() if modules.get(item["module"], False)}
    # Match the existing retail channel rule.
    from app.models.entities import Tenant
    if db.get(Tenant, actor.tenant_id).business_profile == "retail": result.pop("sales", None)
    return result


def require_object(code, actor, db):
    item = accessible(actor, db).get(code)
    if not item: raise HTTPException(status_code=403, detail="El objeto de migración no está habilitado para tu empresa y usuario")
    return item


def groups(objects):
    groups = [{"code": "base", "label": "Maestros comunes", "objects": [code for code in CORE_MODULES if code in objects and code not in {"inventory", "sales", "purchases"}]}]
    for module, short, label, _, _ in AREAS:
        codes = [code for code, item in objects.items() if item["module"] == module and code not in {"warehouses", "cost_centers"}]
        if codes: groups.append({"code": short.lower(), "label": f"{short} · {label}", "objects": codes})
    return [group for group in groups if group["objects"]]


def order_objects(codes):
    result, visiting = [], set()
    def visit(code):
        if code in result: return
        if code in visiting: raise ValueError("Dependencias circulares de migración")
        visiting.add(code)
        for dependency in DEFINITIONS[code]["dependencies"]:
            if dependency in codes: visit(dependency)
        visiting.remove(code); result.append(code)
    for code in codes: visit(code)
    return result


def select_objects(selected_groups, objects):
    available_groups = {group["code"]: group for group in groups(objects)}
    if not selected_groups or set(selected_groups) - set(available_groups):
        raise HTTPException(status_code=422, detail="Selecciona grupos habilitados en tu empresa")
    codes = set(code for group in selected_groups for code in available_groups[group]["objects"])
    def include(code):
        for dependency in DEFINITIONS[code]["dependencies"]:
            if dependency in objects and dependency not in codes:
                codes.add(dependency); include(dependency)
    for code in list(codes): include(code)
    return order_objects(sorted(codes))


def catalog(actor, db, ready):
    from app.services.migration_files import SHEET_NAMES, MAX_PROJECT_ROWS, MAX_OBJECT_ROWS, MAX_FILE_BYTES, MAX_BATCH_BYTES
    objects = accessible(actor, db)
    suite_ready = enterprise_available(db)
    return {"ready": ready, "migration": "0066_migration_projects.sql", "background_jobs": True, "limits": {"project_rows": MAX_PROJECT_ROWS, "object_rows": MAX_OBJECT_ROWS, "file_bytes": MAX_FILE_BYTES, "batch_bytes": MAX_BATCH_BYTES}, "groups": groups(objects), "objects": [
        {**item, "sheet_name": SHEET_NAMES[code], "schema_ready": suite_ready if code in RESOURCE_BY_CODE else True, "blocked_dependencies": [dep for dep in item["required_dependencies"] if dep not in objects]}
        for code, item in objects.items()
    ]}


def template(code, db):
    item = deepcopy(DEFINITIONS[code])
    return {**item, **supported_material_template(item, db)} if code == "products" else item
