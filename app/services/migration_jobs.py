"""Durable migration intent in the existing staging row, with atomic execution.

HTTP returns before long work starts. A worker holds the project row lock for
the whole business transaction; other instances skip that lock. If a process
restarts, PostgreSQL rolls back its work and the next status read resumes the
persisted intent. No credentials, files or jobs are kept only in process memory.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Lock
from time import monotonic
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.entities import Tenant, TenantMembership
from app.models.migration_projects import MigrationProject
from app.security.auth import Actor, require_second_factor
from app.services import migration_projects as service
from app.services.migration_catalog import accessible
from app.services.migration_progress import Progress, tracking

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="erp-migration")
_guard = Lock()
_scheduled = set()


def pending(project):
    return project.simulation.get("job", {}).get("state") == "queued"


def request(db, actor, project, version, kind, *, digest=None, confirmed=False):
    service.check_version(project, version)
    if pending(project): raise HTTPException(status_code=409, detail="El grupo ya tiene una operación en proceso")
    if kind == "commit":
        require_second_factor(actor)
        if not confirmed: raise HTTPException(status_code=422, detail="Marca la confirmación final del grupo")
        staged = service.items(db, project)
        if project.status != "ready" or not project.simulation.get("can_commit") or project.simulation.get("digest") != digest or service.digest_for(project, staged) != digest:
            raise HTTPException(status_code=409, detail="Simula los archivos actuales antes de confirmar")
    elif kind != "simulate":
        raise HTTPException(status_code=422, detail="Operación no admitida")
    elif not service.items(db, project):
        raise HTTPException(status_code=422, detail="Carga al menos una plantilla antes de simular")
    job = {"id": str(uuid4()), "kind": kind, "state": "queued", "requested_at": datetime.now(timezone.utc).isoformat(), "requested_by": str(actor.user_id)}
    # Only a server-authorized final command can persist this intent.
    if kind == "commit": job["digest"] = digest
    service.advance(db, project, version, simulation={**project.simulation, "job": job})
    return project


def dispatch(project_id):
    identifier = UUID(str(project_id))
    with _guard:
        if identifier in _scheduled or len(_scheduled) >= 16: return
        _scheduled.add(identifier)
    future = _pool.submit(run, identifier)
    def finished(_):
        with _guard: _scheduled.discard(identifier)
    future.add_done_callback(finished)


def run(project_id):
    started, job_id = monotonic(), None
    progress = None
    with SessionLocal() as db:
        try:
            # SKIP LOCKED prevents duplicate workers across server instances.
            project = db.scalar(select(MigrationProject).where(MigrationProject.id == project_id).with_for_update(skip_locked=True))
            if project is None or not pending(project): return
            job = dict(project.simulation["job"]); job_id = job["id"]
            member = db.scalar(select(TenantMembership).where(TenantMembership.tenant_id == project.tenant_id, TenantMembership.user_id == UUID(job["requested_by"]), TenantMembership.is_active.is_(True)))
            tenant = db.get(Tenant, project.tenant_id)
            if not member or member.role_code != "owner" or not tenant or not tenant.is_active:
                raise HTTPException(status_code=403, detail="La persona solicitante ya no tiene autorización para migrar en esta empresa")
            actor = Actor(user_id=member.user_id, tenant_id=project.tenant_id, role_code=member.role_code, assurance_level="aal2" if job["kind"] == "commit" else "aal1")
            if set(project.object_codes) - set(accessible(actor, db)):
                raise HTTPException(status_code=403, detail="Cambió la asignación de módulos. Revisa los accesos del grupo")
            progress = Progress(project.tenant_id, project.id, job_id, service.items(db, project))
            with tracking(progress):
                if job["kind"] == "simulate":
                    service.simulate(db, actor, project, project.version)
                else:
                    service.commit(db, actor, project, project.version, job["digest"], True)
                progress.finalizing()
                project.simulation = {**project.simulation, "job": {**job, "state": "completed", "progress": progress.snapshot(completed=True), "finished_at": datetime.now(timezone.utc).isoformat(), "seconds": round(monotonic() - started, 2)}}
                db.commit()
        except Exception as error:
            db.rollback()
            if job_id is None: return
            project = db.scalar(select(MigrationProject).where(MigrationProject.id == project_id).with_for_update())
            if project is None or project.simulation.get("job", {}).get("id") != job_id or not pending(project): return
            detail = error.detail if isinstance(error, HTTPException) else "El servidor interrumpió la operación. No se guardaron datos parciales. Revisa el grupo antes de reintentar."
            message = detail if isinstance(detail, str) else detail.get("message", "Revisa los errores del grupo") if isinstance(detail, dict) else "Revisa los errores del grupo"
            simulation = {**project.simulation, "can_commit": False, "job": {**project.simulation["job"], "state": "failed", "message": message, "finished_at": datetime.now(timezone.utc).isoformat()}}
            if progress: simulation["job"]["progress"] = progress.snapshot(failed=True, rolled_back=job["kind"] == "commit")
            if isinstance(detail, dict) and "objects" in detail: simulation["objects"] = detail["objects"]
            service.advance(db, project, project.version, status="errors", simulation=simulation)
            db.commit()
