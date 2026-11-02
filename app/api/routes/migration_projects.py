import io
from hashlib import sha256
from uuid import UUID
from zipfile import ZipFile, BadZipFile, ZIP_DEFLATED

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.migration_projects import MigrationProject
from app.security.auth import Actor, require_owner_or_admin, require_second_factor
from app.services import migration_catalog as catalog, migration_projects as service
from app.services.migration_examples import example_rows
from app.services.migration_workbooks import csv_bytes, workbook_bytes, group_workbook_bytes
from app.services.migration_files import recognize_file, reject, issue, MAX_FILE_BYTES, MAX_BATCH_BYTES, MAX_FILES, SHEET_NAMES

router = APIRouter(prefix="/migration-projects", tags=["migration projects"])


class CreateProject(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID
    name: str = Field(min_length=3, max_length=120)
    groups: list[str] = Field(min_length=1, max_length=20)


class Version(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


class Confirmation(Version):
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed: bool


def finish(db, project):
    try:
        db.commit()
        return service.payload(db, project)
    except IntegrityError as error:
        db.rollback(); raise HTTPException(status_code=409, detail="Otro proceso modificó el grupo. Actualiza antes de continuar") from error


@router.get("/catalog")
def objects(actor: Actor = Depends(require_owner_or_admin), db: Session = Depends(get_db)):
    return catalog.catalog(actor, db, service.available(db))


@router.get("")
def projects(actor: Actor = Depends(require_owner_or_admin), db: Session = Depends(get_db)):
    if not service.available(db): return {"ready": False, "projects": []}
    allowed = set(catalog.accessible(actor, db))
    rows = db.scalars(select(MigrationProject).where(MigrationProject.tenant_id == actor.tenant_id).order_by(MigrationProject.created_at.desc()).limit(200)).all()
    return {"ready": True, "projects": [{"id": row.id, "name": row.name, "groups": row.group_codes, "status": row.status, "created_by": row.created_by, "created_at": row.created_at, "object_count": len(row.object_codes)} for row in rows if set(row.object_codes) <= allowed]}


@router.post("", status_code=201)
def create(body: CreateProject, actor: Actor = Depends(require_owner_or_admin), db: Session = Depends(get_db)):
    return finish(db, service.create_project(db, actor, body))


@router.get("/{project_id}")
def detail(project_id: UUID, actor: Actor = Depends(require_owner_or_admin), db: Session = Depends(get_db)):
    return service.payload(db, service.get_project(db, actor, project_id))


def workbook_template(code, db, prefix):
    template = catalog.template(code, db)
    example = example_rows(prefix)[code][0]
    template["example"] = [example.get(key, "") for key in template["columns"]]
    return template


@router.get("/objects/{code}/template")
def download_template(code: str, example: bool = False, format: str = Query(default="xlsx", pattern="^(csv|xlsx)$"), project_id: UUID | None = None, actor: Actor = Depends(require_owner_or_admin), db: Session = Depends(get_db)):
    catalog.require_object(code, actor, db)
    prefix = project_id.hex[:8].upper() if project_id else "DEMO"
    if project_id:
        project = service.get_project(db, actor, project_id)
        if code not in project.object_codes: raise HTTPException(status_code=422, detail="Objeto ajeno al grupo")
    template = workbook_template(code, db, prefix)
    rows = [[row.get(key, "") for key in template["columns"]] for row in example_rows(prefix)[code]] if example else []
    content = csv_bytes(template["columns"], rows) if format == "csv" else workbook_bytes(template, rows=rows)
    return Response(content, media_type="text/csv; charset=utf-8" if format == "csv" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{code}.{format}"', "Cache-Control": "no-store"})


@router.get("/{project_id}/example-pack")
def example_pack(project_id: UUID, actor: Actor = Depends(require_owner_or_admin), db: Session = Depends(get_db)):
    project = service.get_project(db, actor, project_id)
    prefix = project.id.hex[:8].upper()
    examples = example_rows(prefix)
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for code in project.object_codes:
            template = workbook_template(code, db, prefix)
            archive.writestr(f"{code}.xlsx", workbook_bytes(template, rows=[[row.get(key, "") for key in template["columns"]] for row in examples[code]]))
        archive.writestr("LEEME.txt", "Datos ficticios para una empresa de evaluación.\nSube este ZIP al mismo grupo y pulsa Simular grupo. Revisa el efecto sobre stock/cuentas antes de confirmar.\nNo mezcles saldos iniciales con compras que ya estén incluidas en esos saldos.\nLos documentos empresariales conservan su estado inicial y las aprobaciones posteriores.\n".encode("utf-8"))
    return Response(output.getvalue(), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="ejemplos-migracion.zip"', "Cache-Control": "no-store"})


@router.post("/{project_id}/objects/{code}/file")
async def upload(project_id: UUID, code: str, version: int = Form(..., ge=1), file: UploadFile = File(...), actor: Actor = Depends(require_owner_or_admin), db: Session = Depends(get_db)):
    project = service.get_project(db, actor, project_id, lock=True)
    service.check_version(project, version)
    content = await file.read(MAX_FILE_BYTES + 1)
    found = recognize_file(file.filename or code + ".xlsx", content, project.object_codes, expected=code)
    return stage_recognized(db, actor, project, [(item, sha256(content).hexdigest()) for item in found])


def stage_recognized(db, actor, project, recognized):
    initial_version = project.version
    seen, duplicates = set(), []
    for item, _ in recognized:
        if item.code in seen: duplicates.append(issue(item.filename, item.sheet, "La misma plantilla aparece más de una vez en el envío. Conserva una sola hoja por objeto.", code=item.code))
        seen.add(item.code)
    if duplicates: reject(duplicates)
    logs = []
    try:
        for item, digest in recognized:
            service.stage(db, actor, project, item.code, item.rows, item.filename, digest, project.version)
            logs.extend(item.messages)
        project.simulation = {**(project.simulation if project.version == initial_version else {}), "upload_logs": logs}
        return finish(db, project)
    except HTTPException as error:
        db.rollback()
        reject([issue(item.filename, item.sheet, str(error.detail), code=item.code)])
    except Exception:
        db.rollback(); raise


@router.post("/{project_id}/upload-bulk")
async def upload_bulk(project_id: UUID, version: int = Form(..., ge=1), files: list[UploadFile] = File(...), actor: Actor = Depends(require_owner_or_admin), db: Session = Depends(get_db)):
    project = service.get_project(db, actor, project_id, lock=True)
    service.check_version(project, version)
    if len(files) > MAX_FILES: reject([issue("", "", f"Selecciona hasta {MAX_FILES} archivos por envío")])
    recognized, logs, total = [], [], 0
    for file in files:
        data = await file.read(MAX_FILE_BYTES + 1)
        total += len(data)
        if total > MAX_BATCH_BYTES: reject([issue(file.filename or "", "", "El envío supera 40 MB. Divide los archivos en varios envíos al mismo grupo.")])
        try:
            recognized.extend((item, sha256(data).hexdigest()) for item in recognize_file(file.filename or "", data, project.object_codes))
        except HTTPException as error:
            logs.extend(error.detail["logs"])
    if logs: reject(logs)
    return stage_recognized(db, actor, project, recognized)


@router.post("/{project_id}/upload-pack")
async def upload_pack(project_id: UUID, version: int = Form(..., ge=1), file: UploadFile = File(...), actor: Actor = Depends(require_owner_or_admin), db: Session = Depends(get_db)):
    project = service.get_project(db, actor, project_id, lock=True)
    service.check_version(project, version)
    content = await file.read(MAX_BATCH_BYTES + 1)
    if len(content) > MAX_BATCH_BYTES: raise HTTPException(status_code=422, detail="El ZIP supera 40 MB")
    recognized = []
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            entries = [item for item in archive.infolist() if not item.is_dir() and item.filename.lower().endswith((".xlsx", ".csv"))]
            if not entries or len(entries) > MAX_FILES or sum(item.file_size for item in archive.infolist()) > MAX_BATCH_BYTES:
                raise ValueError("El paquete está vacío o supera los límites del grupo")
            for entry in entries:
                if "/" in entry.filename or "\\" in entry.filename or entry.file_size > MAX_FILE_BYTES: raise ValueError("Usa los archivos en la raíz del ZIP; máximo 20 MB por Excel")
                with archive.open(entry) as stream: data = stream.read(MAX_FILE_BYTES + 1)
                recognized.extend((item, sha256(data).hexdigest()) for item in recognize_file(entry.filename, data, project.object_codes))
    except (BadZipFile, ValueError) as error:
        db.rollback(); raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception:
        db.rollback(); raise
    return stage_recognized(db, actor, project, recognized)


@router.post("/{project_id}/objects/{code}/remove")
def remove(project_id: UUID, code: str, body: Version, actor: Actor = Depends(require_owner_or_admin), db: Session = Depends(get_db)):
    return finish(db, service.remove_file(db, service.get_project(db, actor, project_id, lock=True), code, body.version))


@router.get("/{project_id}/workbook")
def group_workbook(project_id: UUID, example: bool = False, actor: Actor = Depends(require_owner_or_admin), db: Session = Depends(get_db)):
    project = service.get_project(db, actor, project_id)
    prefix = project.id.hex[:8].upper()
    templates = [workbook_template(code, db, prefix) for code in project.object_codes]
    content = group_workbook_bytes(templates, example_rows(prefix) if example else None)
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": 'attachment; filename="Plantillas del grupo.xlsx"', "Cache-Control": "no-store"})


@router.post("/{project_id}/simulate")
def simulate(project_id: UUID, body: Version, actor: Actor = Depends(require_owner_or_admin), db: Session = Depends(get_db)):
    try:
        project = service.get_project(db, actor, project_id, lock=True)
        return finish(db, service.simulate(db, actor, project, body.version))
    except Exception:
        db.rollback(); raise


@router.post("/{project_id}/commit")
def commit(project_id: UUID, body: Confirmation, actor: Actor = Depends(require_owner_or_admin), _: Actor = Depends(require_second_factor), db: Session = Depends(get_db)):
    try:
        project = service.get_project(db, actor, project_id, lock=True)
        service.commit(db, actor, project, body.version, body.digest, body.confirmed)
        return finish(db, project)
    except OperationalError as error:
        db.rollback(); raise HTTPException(status_code=503, detail="La base no pudo completar el grupo. Actualiza el estado antes de reintentar") from error
    except Exception:
        db.rollback(); raise
