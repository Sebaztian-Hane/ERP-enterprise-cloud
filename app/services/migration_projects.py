"""Stage once, simulate the complete dependency graph, commit atomically."""

from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import PurePath
from uuid import UUID, uuid4
import json

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import func, inspect, select, text, update
from sqlalchemy.exc import IntegrityError

from app.api.routes import imports
from app.models.entities import Customer, InventoryMovement, Product, Supplier, UnitOfMeasure
from app.models.enterprise import EnterpriseRecord
from app.models.migration_projects import MigrationObject, MigrationProject
from app.schemas.enterprise import RecordCreate
from app.services import enterprise
from app.services.enterprise_catalog import RESOURCE_BY_CODE
from app.services.idempotency import canonical_hash
from app.services.migration_catalog import DEFINITIONS, accessible, column_name, order_objects, select_objects
from app.services.audit import record as audit

MAX_PROJECT_ROWS = 2000
MAX_PROJECT_BYTES = 10 * 1024 * 1024


def available(db):
    inspector = inspect(db.connection())
    return inspector.has_table("migration_projects") and inspector.has_table("migration_project_objects")


def ensure_ready(db):
    if not available(db): raise HTTPException(status_code=503, detail="Activa 0066_migration_projects.sql para guardar los grupos de migración")


def get_project(db, actor, identifier, *, lock=False):
    ensure_ready(db)
    query = select(MigrationProject).where(MigrationProject.id == identifier, MigrationProject.tenant_id == actor.tenant_id)
    project = db.scalar(query.with_for_update() if lock else query)
    if not project: raise HTTPException(status_code=404, detail="Grupo de migración no encontrado")
    if set(project.object_codes) - set(accessible(actor, db)):
        raise HTTPException(status_code=403, detail="El grupo contiene módulos que ya no tienes habilitados. Solicita revisar tu asignación")
    return project


def items(db, project):
    return db.scalars(select(MigrationObject).where(MigrationObject.tenant_id == project.tenant_id, MigrationObject.project_id == project.id)).all()


def payload(db, project):
    staged = {item.object_code: item for item in items(db, project)}
    reports = project.simulation.get("objects", {})
    return {"id": project.id, "name": project.name, "groups": project.group_codes, "status": project.status, "version": project.version, "created_by": project.created_by, "created_at": project.created_at, "updated_at": project.updated_at, "simulation": project.simulation, "receipt": project.receipt, "objects": [
        {"code": code, "filename": staged[code].filename if code in staged else None, "rows": len(staged[code].rows) if code in staged else 0, "file_sha256": staged[code].file_sha256 if code in staged else None, "status": "migrated" if project.status == "migrated" and code in staged else reports.get(code, {}).get("status", "uploaded" if code in staged else "empty"), "report": reports.get(code)} for code in project.object_codes
    ]}


def create_project(db, actor, body):
    ensure_ready(db)
    existing = db.get(MigrationProject, body.request_id)
    if existing:
        if existing.tenant_id != actor.tenant_id or existing.created_by != actor.user_id or existing.name != body.name or existing.group_codes != body.groups:
            raise HTTPException(status_code=409, detail="La solicitud de creación ya fue utilizada")
        return get_project(db, actor, existing.id)
    objects = accessible(actor, db)
    codes = select_objects(body.groups, objects)
    project = MigrationProject(id=body.request_id, tenant_id=actor.tenant_id, name=body.name, group_codes=body.groups, object_codes=codes, created_by=actor.user_id, status="draft", version=1, simulation={}, receipt={})
    db.add(project); db.flush()
    audit(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="migration_project_create", module="imports", entity="migration_project", entity_id=project.id)
    return project


def check_version(project, version):
    if project.status == "migrated": raise HTTPException(status_code=409, detail="El grupo ya fue migrado. Crea otro grupo para nuevas altas")
    if project.version != version: raise HTTPException(status_code=409, detail="El grupo cambió. Actualiza antes de continuar")


def advance(db, project, expected, **changes):
    result = db.execute(update(MigrationProject).where(MigrationProject.id == project.id, MigrationProject.tenant_id == project.tenant_id, MigrationProject.version == expected).values(**changes, version=expected + 1, updated_at=func.now()).execution_options(synchronize_session=False))
    if result.rowcount != 1: raise HTTPException(status_code=409, detail="El grupo cambió durante la operación")
    db.refresh(project)


def stage(db, actor, project, code, rows, filename, digest, version):
    if code not in project.object_codes: raise HTTPException(status_code=422, detail="El objeto no pertenece a este grupo")
    existing = {item.object_code: item for item in items(db, project)}
    prior = existing.get(code)
    if prior and prior.file_sha256 == digest and project.status != "migrated": return project
    check_version(project, version)
    unknown = set().union(*(set(row) for row in rows)) - set(DEFINITIONS[code]["columns"]) - {"__source_row__"}
    if unknown: raise HTTPException(status_code=422, detail="Columnas no reconocidas: " + ", ".join(sorted(unknown)))
    # Excel serial dates arrive as datetime at midnight. Persist calendar dates
    # before JSON encoding so both the legacy and enterprise validators agree.
    normalized = jsonable_encoder([{key: value.date().isoformat() if isinstance(value, datetime) else value.isoformat() if isinstance(value, date) else value for key, value in row.items()} for row in rows])
    all_rows = [row for key, item in existing.items() if key != code for row in item.rows] + normalized
    if len(all_rows) > MAX_PROJECT_ROWS or len(json.dumps(all_rows, ensure_ascii=False).encode("utf-8")) > MAX_PROJECT_BYTES:
        raise HTTPException(status_code=422, detail="El grupo admite hasta 2000 filas y 10 MB de datos preparados")
    safe_name = PurePath(filename.replace("\\", "/")).name[:200]
    if prior:
        prior.rows = normalized; prior.filename = safe_name; prior.file_sha256 = digest; prior.uploaded_by = actor.user_id
    else:
        db.add(MigrationObject(tenant_id=actor.tenant_id, project_id=project.id, object_code=code, rows=normalized, filename=safe_name, file_sha256=digest, uploaded_by=actor.user_id))
    db.flush()
    advance(db, project, version, status="uploaded", simulation={})
    return project


def remove_file(db, project, code, version):
    check_version(project, version)
    for item in items(db, project):
        if item.object_code == code: db.delete(item)
    db.flush()
    advance(db, project, version, status="uploaded" if items(db, project) else "draft", simulation={})
    return project


def _resolve_code(db, actor, target, value):
    value = str(value).strip()
    if target.startswith("core:"):
        table, code, _, module, _ = enterprise.CORE[target.removeprefix("core:")]
        if not enterprise.module_is_enabled(db, actor, module): raise ValueError("El módulo de la referencia no está habilitado")
        found = db.execute(enterprise._core_query(f"select id from {table} where tenant_id=:tenant and lower({code})=:code", "tenant"), {"tenant": actor.tenant_id, "code": value.lower()}).scalars().all()
    else:
        found = db.scalars(select(EnterpriseRecord.id).where(EnterpriseRecord.tenant_id == actor.tenant_id, EnterpriseRecord.resource_code == target, func.lower(EnterpriseRecord.number) == value.lower())).all()
    if len(found) != 1: raise ValueError(f"Referencia {target}: {value} no encontrada o ambigua. Carga su plantilla predecesora o revisa el código")
    return str(UUID(str(found[0])))


def _enterprise_rows(code, rows, actor, db):
    enterprise.ensure_ready(db)
    resource = RESOURCE_BY_CODE[code]
    grouped = defaultdict(list)
    for row in rows: grouped[str(row.get("number", "")).strip()].append(row)
    count = 0
    for number, lines in grouped.items():
        first = lines[0]
        if len(lines) > 1 and code != "bills_of_material": raise ValueError(f"Número repetido: {number}")
        data = {}
        for field in resource.fields:
            if field.kind == "lines":
                header = {k: v for k, v in first.items() if k not in {"component_sku", "component_quantity", "__source_row__"}}
                if any({k: v for k, v in row.items() if k not in {"component_sku", "component_quantity", "__source_row__"}} != header for row in lines):
                    raise ValueError(f"La estructura {number} tiene cabeceras distintas entre componentes")
                data[field.key] = [{"product_id": _resolve_code(db, actor, "core:products", row.get("component_sku", "")), "quantity": row.get("component_quantity")} for row in lines]
            else:
                value = first.get(column_name(field))
                if value not in (None, ""):
                    data[field.key] = _resolve_code(db, actor, field.reference, value) if field.reference else value
        command = RecordCreate(number=number, title=first.get("title", ""), data=data)
        enterprise.create(resource, command, actor, db, migration_draft=True)
        count += 1
    return count, 0


def _extra_rows(code, rows, actor, db):
    created, reused, seen = 0, 0, set()
    for row in rows:
        key, name = str(row.get("code") or "").strip(), str(row.get("name") or "").strip()
        if not key or len(key) > (16 if code == "units" else 40) or not name or len(name) > 80:
            raise ValueError("Completa code (hasta 16 caracteres para unidades) y name (hasta 80)")
        if key.casefold() in seen: raise ValueError(f"Código duplicado: {key}")
        seen.add(key.casefold())
        if code == "units":
            existing = db.scalar(select(UnitOfMeasure).where(UnitOfMeasure.tenant_id == actor.tenant_id, func.lower(UnitOfMeasure.code) == key.lower()))
            if existing:
                if existing.name != name or not existing.is_active: raise ValueError(f"La unidad {key} ya existe con otros datos o está inactiva")
                reused += 1; continue
            db.add(UnitOfMeasure(tenant_id=actor.tenant_id, code=key, name=name))
        else:
            existing = db.execute(enterprise._core_query("select name,is_active from cost_centers where tenant_id=:tenant and lower(code)=:code", "tenant"), {"tenant": actor.tenant_id, "code": key.lower()}).first()
            if existing:
                if existing.name != name or not existing.is_active: raise ValueError(f"El centro {key} ya existe con otros datos o está inactivo")
                reused += 1; continue
            db.execute(enterprise._core_query("insert into cost_centers(id,tenant_id,code,name,is_active) values(:id,:tenant,:code,:name,true)", "id", "tenant"), {"id": uuid4(), "tenant": actor.tenant_id, "code": key, "name": name})
        created += 1; db.flush()
    return created, reused


def _core_rows(code, rows, actor, db):
    valid, errors = imports._validate_rows(code, rows)
    if errors: raise HTTPException(status_code=422, detail=errors)
    conflicts = imports._check_database_conflicts(code, valid, actor, db)
    if conflicts: raise ValueError("; ".join(conflicts))
    if code == "products":
        errors = imports._product_references(valid, actor, db)
        if any(row.get("material_profile") for row in valid) and not imports.material_profiles_available(db): errors.append("Activa la ficha industrial 0064")
        if errors: raise ValueError("; ".join(errors))
    if code in {"inventory", "sales", "purchases"}:
        errors = imports._transaction_references(code, valid, actor, db)
        if errors: raise ValueError("; ".join(errors))
        products, _ = imports._reference_maps(actor, db)
        if any(products[row["product_sku"]].tracking_mode != "none" for row in valid):
            raise ValueError("Los materiales con lotes/series requieren su operación específica de inventario")
        if code == "inventory":
            refs = {row["reference"] for row in valid}
            if db.scalar(select(InventoryMovement.id).where(InventoryMovement.tenant_id == actor.tenant_id, InventoryMovement.reference.in_(refs)).limit(1)):
                raise ValueError("Una referencia de existencias ya fue registrada; no se duplicó el stock")
        else:
            model = Customer if code == "sales" else Supplier
            grouped = defaultdict(list)
            for row in valid: grouped[row["number"]].append(row)
            for number, lines in grouped.items():
                if len({(line["counterpart_tax_id"], line["counterpart_name"], line["warehouse_code"], line["due_date"]) for line in lines}) != 1:
                    raise ValueError(f"{number}: las líneas deben compartir contraparte, almacén y vencimiento")
                first = lines[0]
                counterparts = db.scalars(select(model).where(model.tenant_id == actor.tenant_id, model.tax_id == first["counterpart_tax_id"], model.is_active.is_(True))).all()
                if len(counterparts) != 1: raise ValueError("Carga primero la plantilla de clientes/proveedores con una identidad fiscal única y activa")
        return imports._persist_transactions(code, valid, actor, db), 0
    return imports._persist_master(code, valid, actor, db), 0


def _message(error):
    if isinstance(error, HTTPException):
        return error.detail if isinstance(error.detail, list) else [{"row": None, "message": str(error.detail)}]
    if isinstance(error, ValidationError):
        return [{"row": None, "message": ".".join(str(x) for x in item["loc"]) + ": " + item["msg"]} for item in error.errors()]
    if isinstance(error, IntegrityError): return [{"row": None, "message": "Código duplicado o referencia incompatible. No se guardaron datos de este objeto"}]
    return [{"row": None, "message": str(error)}]


def digest_for(project, staged):
    return canonical_hash({"project": str(project.id), "objects": project.object_codes, "files": sorted((item.object_code, item.file_sha256, canonical_hash(item.rows), DEFINITIONS[item.object_code]["version"]) for item in staged)})


def run_objects(db, actor, project, staged):
    reports = {}
    by_code = {item.object_code: item for item in staged}
    current = accessible(actor, db)
    for code in order_objects(project.object_codes):
        if code not in by_code: continue
        report = {"rows": len(by_code[code].rows), "created": 0, "reused": 0, "status": "error", "errors": []}
        reports[code] = report
        try:
            if code not in current or any(dep not in current for dep in DEFINITIONS[code]["required_dependencies"]):
                raise ValueError("El objeto o un módulo predecesor no está habilitado")
            if any(reports.get(dep, {}).get("status") == "error" for dep in DEFINITIONS[code]["dependencies"]):
                raise ValueError("Corrige primero las incidencias de la plantilla predecesora")
            with db.begin_nested():
                count, reused = _enterprise_rows(code, by_code[code].rows, actor, db) if code in RESOURCE_BY_CODE else _extra_rows(code, by_code[code].rows, actor, db) if code in {"units", "cost_centers"} else _core_rows(code, by_code[code].rows, actor, db)
                db.flush()
            report.update(created=count, reused=reused, status="ready")
        except (HTTPException, ValueError, IntegrityError) as error:
            report["errors"] = _message(error)[:50]
    return reports


def simulate(db, actor, project, version):
    staged = items(db, project)
    digest = digest_for(project, staged)
    if project.simulation.get("input_version") == version and project.simulation.get("digest") == digest: return project
    check_version(project, version)
    if not staged: raise HTTPException(status_code=422, detail="Carga al menos una plantilla antes de simular")
    # All the same writes, constraints, references and accounting commands are
    # exercised under a savepoint. No outbox event becomes externally visible.
    savepoint = db.begin_nested()
    try:
        reports = run_objects(db, actor, project, staged)
    finally:
        savepoint.rollback()
    result = {"digest": digest, "input_version": version, "simulated_at": datetime.now(timezone.utc).isoformat(), "simulated_by": str(actor.user_id), "can_commit": bool(reports) and all(item["status"] == "ready" for item in reports.values()), "objects": reports, "total_created": sum(item["created"] for item in reports.values()), "total_rows": sum(len(item.rows) for item in staged)}
    advance(db, project, version, status="ready" if result["can_commit"] else "errors", simulation=result)
    return project


def commit(db, actor, project, version, digest, confirmed):
    if not confirmed: raise HTTPException(status_code=422, detail="Marca la confirmación final del grupo")
    if project.status == "migrated":
        if project.receipt.get("digest") != digest: raise HTTPException(status_code=409, detail="La confirmación no coincide con el grupo migrado")
        return project
    check_version(project, version)
    staged = items(db, project)
    if project.status != "ready" or not project.simulation.get("can_commit") or project.simulation.get("digest") != digest or digest_for(project, staged) != digest:
        raise HTTPException(status_code=409, detail="Simula los archivos actuales antes de confirmar")
    # Recheck current database state automatically, without asking the user to
    # repeat the file-validation workflow. Any error rolls back the whole unit.
    reports = run_objects(db, actor, project, staged)
    if not reports or any(item["status"] != "ready" for item in reports.values()):
        raise HTTPException(status_code=422, detail={"message": "Los datos cambiaron o hay una incidencia. Se revirtió todo el grupo; revisa los mensajes y simula nuevamente", "objects": reports})
    receipt = {"project_id": str(project.id), "digest": digest, "committed_at": datetime.now(timezone.utc).isoformat(), "committed_by": str(actor.user_id), "objects": reports, "total_created": sum(item["created"] for item in reports.values())}
    advance(db, project, version, status="migrated", receipt=receipt)
    audit(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="migration_project_commit", module="imports", entity="migration_project", entity_id=project.id, detail=f"SHA256={digest}; documentos={receipt['total_created']}")
    return project
