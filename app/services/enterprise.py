"""Tenant-scoped operational lifecycle with typed payloads and append-only evidence."""

import json
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import bindparam, func, inspect, select, text, update
from sqlalchemy.dialects.postgresql import UUID as UUIDType
from sqlalchemy.orm import Session

from app.models.enterprise import EnterpriseRecord, EnterpriseRecordEvent
from app.models.entities import Product, Tenant
from app.security.auth import Actor, ROLE_PERMISSIONS, module_is_enabled, require_permission, require_second_factor
from app.services.audit import record as audit
from app.services.enterprise_catalog import AREAS, RESOURCE_BY_CODE, SCHEMAS, Resource
from app.services.idempotency import canonical_hash

CORE = {
    "products": ("products", "sku", "name", "products", True),
    "customers": ("customers", "code", "name", "customers", True),
    "suppliers": ("suppliers", "code", "name", "suppliers", True),
    "warehouses": ("warehouses", "code", "name", "inventory", True),
    "cost_centers": ("cost_centers", "code", "name", "operations", True),
    "purchase_orders": ("purchase_orders", "number", "number", "purchase_orders", False),
    "deliveries": ("sales_deliveries", "number", "number", "sales_orders", False),
}


def _core_query(sql, *identity_keys):
    """Keep UUID binding typed on PostgreSQL and isolated SQLite test stores."""
    return text(sql).bindparams(*(bindparam(key, type_=UUIDType(as_uuid=True)) for key in identity_keys))


def available(db: Session) -> bool:
    inspector = inspect(db.connection())
    return inspector.has_table("enterprise_records") and inspector.has_table("enterprise_record_events")


def ensure_ready(db: Session):
    if not available(db):
        raise HTTPException(status_code=503, detail="Falta activar las tablas empresariales. Ejecuta la migración 0065_enterprise_modules.sql del backend.")


def definition(code: str) -> Resource:
    item = RESOURCE_BY_CODE.get(code)
    if item is None:
        raise HTTPException(status_code=404, detail="Tipo de documento no encontrado")
    return item


def allowed(actor: Actor, resource: Resource, action: str, db: Session) -> bool:
    grants = ROLE_PERMISSIONS.get(actor.role_code, set())
    return ("*" in grants or f"{resource.module}:{action}" in grants) and module_is_enabled(db, actor, resource.module)


def authorize(code: str, action: str, actor: Actor, db: Session) -> Resource:
    resource = definition(code)
    require_permission(f"{resource.module}:{action}", resource.module)(actor, db)
    return resource


def catalog(actor: Actor, db: Session):
    ready = available(db)
    modules = {}
    grants = ROLE_PERMISSIONS.get(actor.role_code, set())
    for code, _, _, _, _ in AREAS:
        modules[code] = module_is_enabled(db, actor, code)
    resources = []
    for resource in RESOURCE_BY_CODE.values():
        access = {action: ("*" in grants or f"{resource.module}:{action}" in grants) and modules[resource.module] for action in ("read", "write", "approve")}
        if access["read"]:
            resources.append({**asdict(resource), "access": access})
    visible = {item["module"] for item in resources}
    legacy = {"operations": "accounting:read", "documents": "documents:read"}
    areas = [{"code": code, "short": short, "label": label, "owner": owner, "href": href} for code, short, label, owner, href in AREAS if code in visible or (code in legacy and modules[code] and ("*" in grants or legacy[code] in grants))]
    return {"ready": ready, "migration": "0065_enterprise_modules.sql", "areas": areas, "resources": resources}


def get_record(db: Session, actor: Actor, record_id: UUID, *, lock=False) -> EnterpriseRecord:
    statement = select(EnterpriseRecord).where(EnterpriseRecord.id == record_id, EnterpriseRecord.tenant_id == actor.tenant_id)
    row = db.scalar(statement.with_for_update() if lock else statement)
    if row is None:
        raise HTTPException(status_code=404, detail="Documento no encontrado en la empresa activa")
    return row


def payload(row: EnterpriseRecord) -> dict:
    return {key: getattr(row, key) for key in ("id", "resource_code", "module_code", "number", "title", "status", "version", "data", "derived", "created_by", "updated_by", "created_at", "updated_at")}


def options(resource: Resource, field_key: str, actor: Actor, db: Session, q: str = "", selected: UUID | None = None):
    field = next((item for item in resource.fields if item.key == field_key), None)
    target = field.reference if field else "core:products" if field_key == "components.product_id" and resource.code == "bills_of_material" else None
    if target is None:
        raise HTTPException(status_code=404, detail="Referencia no disponible")
    if target.startswith("core:"):
        table, code, label, module, active = CORE[target.split(":", 1)[1]]
        if not module_is_enabled(db, actor, module):
            raise HTTPException(status_code=403, detail="El módulo de la referencia no está habilitado")
        # Identifiers come exclusively from CORE, never from a request.
        clause = " and id=:selected" if selected else " and is_active=true" if active else " and status not in ('cancelled','rejected')"
        statement = _core_query(f"select id,{code} as code,{label} as label from {table} where tenant_id=:tenant{clause} and (lower({code}) like :q or lower({label}) like :q) order by {code} limit 100", "tenant", *(("selected",) if selected else ()))
        return [{**dict(row), "id": str(UUID(str(row["id"])))} for row in db.execute(statement, {"tenant": actor.tenant_id, "q": f"%{q.strip().lower()}%", "selected": selected}).mappings().all()]
    ensure_ready(db)
    # Cross-document options deliberately return code/title only. HCM fields
    # never appear outside HCM resource definitions.
    rows = db.scalars(select(EnterpriseRecord).where(EnterpriseRecord.tenant_id == actor.tenant_id, EnterpriseRecord.resource_code == target, EnterpriseRecord.id == selected if selected else EnterpriseRecord.status.not_in(["cancelled", "rejected", "inactive", "disposed"]), (EnterpriseRecord.number.ilike(f"%{q}%")) | (EnterpriseRecord.title.ilike(f"%{q}%"))).order_by(EnterpriseRecord.number).limit(100)).all()
    return [{"id": row.id, "code": row.number, "label": row.title, "status": row.status} for row in rows]


def _reference(target, value, actor, db, *, active=True):
    if target.startswith("core:"):
        table, _, _, module, has_active = CORE[target.split(":", 1)[1]]
        if not module_is_enabled(db, actor, module):
            raise HTTPException(status_code=403, detail="El módulo de una referencia no está habilitado")
        clause = " and is_active=true" if active and has_active else " and status not in ('cancelled','rejected')" if not has_active else ""
        row = db.execute(_core_query(f"select id from {table} where id=:id and tenant_id=:tenant{clause}", "id", "tenant"), {"id": UUID(str(value)), "tenant": actor.tenant_id}).first()
        if row is None:
            raise HTTPException(status_code=422, detail="Referencia inexistente, inactiva o ajena a la empresa")
        return None
    row = get_record(db, actor, UUID(str(value)), lock=True)
    if row.resource_code != target or row.status in {"cancelled", "rejected", "disposed"}:
        raise HTTPException(status_code=422, detail="El documento relacionado no corresponde o fue cancelado")
    return row


def validate_data(resource: Resource, raw: dict, actor: Actor, db: Session, *, target_state=None, record_id=None) -> tuple[dict, dict]:
    if len(json.dumps(raw, default=str, ensure_ascii=False).encode("utf-8")) > 65536:
        raise HTTPException(status_code=422, detail="El documento supera 64 KB")
    try:
        model = SCHEMAS[resource.code].model_validate(raw)
    except ValidationError as error:
        issues = [{"loc": item["loc"], "msg": item["msg"], "type": item["type"]} for item in error.errors()]
        raise HTTPException(status_code=422, detail=issues) from error
    data = model.model_dump(mode="json", exclude_none=True)
    refs = {field.key: _reference(field.reference, data[field.key], actor, db, active=target_state is None) for field in resource.fields if field.reference and data.get(field.key)}
    for start, end in (("start_date", "end_date"), ("planned_start", "planned_end"), ("departure_date", "arrival_date")):
        if data.get(start) and data.get(end) and data[end] < data[start]:
            raise HTTPException(status_code=422, detail="La fecha final no puede ser anterior a la inicial")
    required = resource.completion.get(target_state, ())
    if any(key not in data or data[key] is None or (isinstance(data[key], str) and not data[key].strip()) for key in required):
        raise HTTPException(status_code=422, detail="Completa los datos de cierre: " + ", ".join(required))
    if resource.code in {"absences", "attendance"} and (target_state is None or target_state == "approved") and refs["employee_id"].status != "active":
        raise HTTPException(status_code=422, detail="El empleado debe estar activo")
    if resource.code == "employees" and data["contract_type"] != "indefinite" and target_state == "active":
        if not data.get("contract_end_date") or data["contract_end_date"] < data["hire_date"]:
            raise HTTPException(status_code=422, detail="El contrato temporal requiere una fecha final válida")
    if resource.code == "vehicles":
        data["license_plate"] = data["license_plate"].upper()
        duplicate = db.scalar(select(EnterpriseRecord.id).where(EnterpriseRecord.tenant_id == actor.tenant_id, EnterpriseRecord.resource_code == "vehicles", EnterpriseRecord.status != "cancelled", EnterpriseRecord.data["license_plate"].as_string() == data["license_plate"], EnterpriseRecord.id != record_id if record_id else True).limit(1))
        if duplicate: raise HTTPException(status_code=409, detail="La placa ya está registrada en esta empresa")
    if resource.code in {"maintenance_orders", "maintenance_notifications"} and target_state in {None, "released"} and refs["equipment_id"].status != "active":
        raise HTTPException(status_code=422, detail="El equipo debe estar activo")
    if resource.code == "warehouse_tasks" and data["source_bin"].casefold() == data["destination_bin"].casefold():
        raise HTTPException(status_code=422, detail="Las ubicaciones origen y destino deben ser distintas")
    if resource.code == "bills_of_material":
        component_ids = [line["product_id"] for line in data["components"]]
        if len(component_ids) != len(set(component_ids)) or data["product_id"] in component_ids:
            raise HTTPException(status_code=422, detail="Los componentes deben ser únicos y diferentes del producto a fabricar")
        for product_id in component_ids: _reference("core:products", product_id, actor, db)
    if resource.code == "inspections" and target_state == "completed":
        if Decimal(data["accepted_quantity"]) + Decimal(data["rejected_quantity"]) != Decimal(data["inspected_quantity"]):
            raise HTTPException(status_code=422, detail="Aceptado más rechazado debe igualar la cantidad inspeccionada")
    if resource.code == "project_tasks":
        project = refs["project_id"]
        if not project.data["start_date"] <= data["due_date"] <= project.data["end_date"]:
            raise HTTPException(status_code=422, detail="El vencimiento de la tarea debe estar dentro del proyecto")
        if target_state == "in_progress" and project.status not in {"released", "in_progress"}:
            raise HTTPException(status_code=422, detail="El proyecto debe estar liberado")
        if target_state == "completed" and Decimal(data["progress"]) != 100:
            raise HTTPException(status_code=422, detail="Una tarea completada requiere avance de 100%")
    if resource.code == "attendance":
        duplicate = db.scalar(select(EnterpriseRecord.id).where(EnterpriseRecord.tenant_id == actor.tenant_id, EnterpriseRecord.resource_code == "attendance", EnterpriseRecord.status != "cancelled", EnterpriseRecord.data["employee_id"].as_string() == data["employee_id"], EnterpriseRecord.data["work_date"].as_string() == data["work_date"], EnterpriseRecord.id != record_id if record_id else True).limit(1))
        if duplicate:
            raise HTTPException(status_code=409, detail="Ya existe una jornada para este empleado y fecha")
    if resource.code == "absences" and target_state == "approved":
        overlap = db.scalar(select(EnterpriseRecord.id).where(EnterpriseRecord.tenant_id == actor.tenant_id, EnterpriseRecord.resource_code == "absences", EnterpriseRecord.status == "approved", EnterpriseRecord.id != record_id, EnterpriseRecord.data["employee_id"].as_string() == data["employee_id"], EnterpriseRecord.data["start_date"].as_string() <= data["end_date"], EnterpriseRecord.data["end_date"].as_string() >= data["start_date"]).limit(1))
        if overlap:
            raise HTTPException(status_code=409, detail="La ausencia se superpone con una ausencia aprobada")
    if resource.code == "supplier_quotes":
        requisition = refs["requisition_id"]
        if requisition.status not in {"submitted", "approved"}:
            raise HTTPException(status_code=422, detail="La solicitud debe estar enviada o aprobada")
        if target_state == "selected":
            tenant = db.get(Tenant, actor.tenant_id)
            today = datetime.now(ZoneInfo(tenant.timezone)).date().isoformat()
            if requisition.status != "approved" or data["valid_until"] < today:
                raise HTTPException(status_code=422, detail="La solicitud debe estar aprobada y la oferta vigente")
            duplicate = db.scalar(select(EnterpriseRecord.id).where(EnterpriseRecord.tenant_id == actor.tenant_id, EnterpriseRecord.resource_code == "supplier_quotes", EnterpriseRecord.status == "selected", EnterpriseRecord.id != record_id, EnterpriseRecord.data["requisition_id"].as_string() == data["requisition_id"]).limit(1))
            if duplicate: raise HTTPException(status_code=409, detail="La solicitud ya tiene una oferta seleccionada")
    if resource.code == "trips" and target_state == "dispatched":
        if refs["vehicle_id"].status != "active" or refs["freight_order_id"].status not in {"released", "in_progress"}:
            raise HTTPException(status_code=422, detail="El vehículo debe estar activo y la orden liberada")
        if Decimal(refs["vehicle_id"].data["capacity_kg"]) < Decimal(refs["freight_order_id"].data["weight_kg"]):
            raise HTTPException(status_code=422, detail="La carga supera la capacidad del vehículo")
        duplicate = db.scalar(select(EnterpriseRecord.id).where(EnterpriseRecord.tenant_id == actor.tenant_id, EnterpriseRecord.resource_code == "trips", EnterpriseRecord.status == "dispatched", EnterpriseRecord.id != record_id, EnterpriseRecord.data["vehicle_id"].as_string() == data["vehicle_id"]).limit(1))
        if duplicate: raise HTTPException(status_code=409, detail="El vehículo ya tiene un viaje en ejecución")
    if resource.code == "projects" and target_state == "completed":
        unfinished = db.scalar(select(EnterpriseRecord.id).where(EnterpriseRecord.tenant_id == actor.tenant_id, EnterpriseRecord.resource_code == "project_tasks", EnterpriseRecord.data["project_id"].as_string() == str(record_id), EnterpriseRecord.status.not_in(["completed", "cancelled"])).limit(1))
        if unfinished: raise HTTPException(status_code=409, detail="El proyecto tiene tareas sin terminar")
    return data, refs


def add_event(db, row, actor, action, previous, note):
    digest = canonical_hash({"number": row.number, "title": row.title, "status": row.status, "version": row.version, "data": row.data, "derived": row.derived})
    db.add(EnterpriseRecordEvent(tenant_id=actor.tenant_id, record_id=row.id, version=row.version, action=action, from_state=previous, to_state=row.status, actor_user_id=actor.user_id, note=note, content_hash=digest))
    # No HCM payload or personal data is sent to RAG or the generic audit text.
    audit(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action=action, module="enterprise", entity=row.resource_code, entity_id=row.id, detail=f"{previous or '-'} -> {row.status}; versión {row.version}; SHA256={digest}")


def create(resource: Resource, command, actor: Actor, db: Session):
    data, _ = validate_data(resource, command.data, actor, db)
    row = EnterpriseRecord(tenant_id=actor.tenant_id, module_code=resource.module, resource_code=resource.code, number=command.number, title=command.title, data=data, derived={}, status=resource.initial, version=1, created_by=actor.user_id, updated_by=actor.user_id)
    db.add(row); db.flush()
    add_event(db, row, actor, "create", None, "Alta del documento")
    return row


def _save(db, row, actor, expected_version, **changes):
    result = db.execute(update(EnterpriseRecord).where(EnterpriseRecord.id == row.id, EnterpriseRecord.tenant_id == actor.tenant_id, EnterpriseRecord.version == expected_version).values(**changes, version=expected_version + 1, updated_by=actor.user_id, updated_at=func.now()).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="El documento cambió. Actualiza antes de continuar")
    db.refresh(row)


def edit(row, resource, command, actor, db):
    if row.version != command.version:
        raise HTTPException(status_code=409, detail="El documento cambió. Actualiza antes de editar")
    if row.status not in resource.editable:
        raise HTTPException(status_code=409, detail="Este estado no permite editar el documento")
    data, _ = validate_data(resource, command.data, actor, db, record_id=row.id)
    _save(db, row, actor, command.version, number=command.number, title=command.title, data=data)
    add_event(db, row, actor, "edit", row.status, "Actualización de datos")
    return row


def transition(row, resource, command, actor, db):
    if row.version != command.version:
        raise HTTPException(status_code=409, detail="El documento cambió. Actualiza antes de continuar")
    if command.to_state not in resource.transitions.get(row.status, ()):
        raise HTTPException(status_code=409, detail="Transición no permitida desde el estado actual")
    if command.to_state in resource.approvals:
        require_permission(f"{resource.module}:approve", resource.module)(actor, db)
        require_second_factor(actor)
        if row.created_by == actor.user_id:
            raise HTTPException(status_code=403, detail="La aprobación requiere una persona distinta del creador")
    permitted = set(resource.completion.get(command.to_state, ()))
    if set(command.completion) - permitted:
        raise HTTPException(status_code=422, detail="La transición solo admite sus datos de cierre")
    data, refs = validate_data(resource, {**row.data, **command.completion}, actor, db, target_state=command.to_state, record_id=row.id)
    derived = row.derived
    if resource.code == "production_orders" and command.to_state == "released":
        bom = refs["bom_id"]
        if bom.status != "active": raise HTTPException(status_code=422, detail="La lista de materiales debe estar activa")
        product_ids = [UUID(item["product_id"]) for item in bom.data["components"]] + [UUID(bom.data["product_id"])]
        products = {str(item.id): item for item in db.scalars(select(Product).where(Product.tenant_id == actor.tenant_id, Product.id.in_(product_ids), Product.is_active.is_(True))).all()}
        if len(products) != len(product_ids): raise HTTPException(status_code=422, detail="La estructura contiene materiales inexistentes o inactivos")
        factor = Decimal(data["quantity"]) / Decimal(bom.data["base_quantity"])
        derived = {"bom_version": bom.version, "requirements": [{"product_id": item["product_id"], "sku": products[item["product_id"]].sku, "name": products[item["product_id"]].name, "unit": products[item["product_id"]].unit_code, "quantity": str(Decimal(item["quantity"]) * factor)} for item in bom.data["components"]]}
    previous = row.status
    _save(db, row, actor, command.version, data=data, derived=derived, status=command.to_state)
    add_event(db, row, actor, "transition", previous, command.note)
    return row
