from uuid import UUID
from sqlalchemy.orm import Session
from test_migration_projects import suite, create, upload, count
from app.models.entities import Product, UnitOfMeasure
from app.services import migration_jobs


def setup_worker(monkeypatch, suite):
    scheduled = []
    monkeypatch.setattr(migration_jobs, "dispatch", lambda identifier: scheduled.append(identifier))
    monkeypatch.setattr(migration_jobs, "SessionLocal", lambda: Session(suite[1].bind, autoflush=False))
    return scheduled


def enqueue(suite, project, kind="simulate", **body):
    return suite[0].post(f"/api/v1/migration-projects/{project['id']}/{kind}?background=true", json={"version": project["version"], **body})


def read(suite, project):
    suite[1].expire_all()
    return suite[0].get(f"/api/v1/migration-projects/{project['id']}").json()


def test_job_persists_intent_blocks_mutation_and_resumes_from_status_read(suite, monkeypatch):
    scheduled = setup_worker(monkeypatch, suite)
    project = upload(suite, upload(suite, create(suite, ["base"]), "units"), "products")
    response = enqueue(suite, project)
    assert response.status_code == 202, response.text
    queued = response.json()
    assert queued["simulation"]["job"]["state"] == "queued"
    assert count(suite[1], Product) == 0
    suite[1].rollback()
    blocked = suite[0].post(f"/api/v1/migration-projects/{project['id']}/objects/products/remove", json={"version": queued["version"]})
    assert blocked.status_code == 409
    before = len(scheduled)
    assert read(suite, project)["simulation"]["job"]["state"] == "queued"
    assert len(scheduled) == before + 1
    migration_jobs.run(UUID(project["id"]))
    ready = read(suite, project)
    assert ready["simulation"]["job"]["state"] == "completed"
    assert ready["simulation"]["can_commit"]
    assert count(suite[1], Product) == count(suite[1], UnitOfMeasure) == 0


def test_background_commit_requires_confirmation_mfa_and_never_duplicates(suite, monkeypatch):
    setup_worker(monkeypatch, suite)
    project = upload(suite, create(suite, ["base"]), "units")
    enqueue(suite, project); migration_jobs.run(UUID(project["id"]))
    ready = read(suite, project)
    args = {"digest": ready["simulation"]["digest"], "confirmed": False}
    assert enqueue(suite, ready, "commit", **args).status_code == 422
    args["confirmed"] = True
    actor = suite[2]["actor"]
    suite[2]["actor"] = actor.model_copy(update={"assurance_level": "aal1"})
    assert enqueue(suite, ready, "commit", **args).status_code == 403
    suite[2]["actor"] = actor
    response = enqueue(suite, ready, "commit", **args)
    assert response.status_code == 202, response.text
    assert count(suite[1], UnitOfMeasure) == 0
    suite[1].rollback()
    migration_jobs.run(UUID(project["id"]))
    result = read(suite, project)
    assert result["status"] == "migrated"
    assert count(suite[1], UnitOfMeasure) == 1
    suite[1].rollback()
    migration_jobs.run(UUID(project["id"]))
    assert count(suite[1], UnitOfMeasure) == 1


def test_worker_rechecks_owner_membership_before_any_write(suite, monkeypatch):
    setup_worker(monkeypatch, suite)
    project = upload(suite, create(suite, ["base"]), "units")
    assert enqueue(suite, project).status_code == 202
    suite[4].role_code = "admin"; suite[1].commit()
    migration_jobs.run(UUID(project["id"]))
    result = read(suite, project)
    assert result["simulation"]["job"]["state"] == "failed"
    assert not result["simulation"]["can_commit"]
    assert "autorización" in result["simulation"]["job"]["message"]
    assert count(suite[1], UnitOfMeasure) == 0


def test_unexpected_worker_failure_rolls_back_and_records_retriable_error(suite, monkeypatch):
    setup_worker(monkeypatch, suite)
    project = upload(suite, create(suite, ["base"]), "units")
    assert enqueue(suite, project).status_code == 202
    def broken(db, actor, *_):
        db.add(UnitOfMeasure(tenant_id=actor.tenant_id, code="ROLLBACK", name="Temporal")); db.flush()
        raise RuntimeError("private database details must not be returned")
    monkeypatch.setattr(migration_jobs.service, "simulate", broken)
    migration_jobs.run(UUID(project["id"]))
    result = read(suite, project)
    assert result["simulation"]["job"]["state"] == "failed"
    assert "private" not in str(result)
    assert count(suite[1], UnitOfMeasure) == 0
    suite[1].rollback()
    assert enqueue(suite, result).status_code == 202
