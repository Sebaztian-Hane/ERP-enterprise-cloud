from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID, uuid4

from test_migration_projects import suite, create, upload, count
from test_migration_jobs import setup_worker, enqueue, read
from app.models.entities import Product, UnitOfMeasure
from app.models.migration_projects import MigrationProject
from app.services import migration_jobs, migration_progress as progress, migration_projects as service
from app.services.migration_examples import example_rows


def test_real_row_counters_survive_success_but_simulation_writes_roll_back(suite, monkeypatch):
    setup_worker(monkeypatch, suite)
    project = create(suite, ["base", "hcm", "pp"])
    units = example_rows()["units"] + [{"code": f"UNIT-{i}", "name": f"Unidad {i}"} for i in range(2)]
    project = upload(suite, project, "units", units)
    project = upload(suite, project, "products")
    employees = [example_rows(f"EMP{i}")["employees"][0] for i in range(3)]
    project = upload(suite, project, "employees", employees)
    project = upload(suite, project, "bills_of_material")
    assert enqueue(suite, project).status_code == 202
    snapshots = []
    original = progress.Progress.update
    def observe(self, **values):
        original(self, **values)
        snapshots.append(self.snapshot())
    monkeypatch.setattr(progress.Progress, "update", observe)
    migration_jobs.run(UUID(project["id"]))
    result = read(suite, project)
    job = result["simulation"]["job"]
    assert result["simulation"]["can_commit"]
    assert job["state"] == "completed" and job["progress"]["percent"] == 100
    assert job["progress"]["phase"] == "completed" and not job["progress"]["live"]
    for code, rows in {"units": 3, "products": 2, "employees": 3, "bills_of_material": 1}.items():
        final = job["progress"]["objects"][code]
        assert final["total_rows"] == final["validated_rows"] == final["prepared_rows"] == rows
        assert final["state"] == "processed" and final["percent"] == 100
        intermediate = [s["objects"][code] for s in snapshots if s["current_object"] == code]
        assert any(0 < item["percent"] < 100 for item in intermediate)
    assert all(snapshot["percent"] < 100 for snapshot in snapshots)
    assert count(suite[1], Product) == count(suite[1], UnitOfMeasure) == 0
    assert progress.live_snapshot(suite[2]["actor"].tenant_id, project["id"], job["id"]) is None


def test_final_load_failure_marks_prepared_objects_reverted_without_partial_writes(suite, monkeypatch):
    setup_worker(monkeypatch, suite)
    project = upload(suite, upload(suite, create(suite, ["base"]), "units"), "products")
    enqueue(suite, project); migration_jobs.run(UUID(project["id"]))
    ready = read(suite, project)
    assert enqueue(suite, ready, "commit", confirmed=True, digest=ready["simulation"]["digest"]).status_code == 202
    def conflict(*_): raise ValueError("Conflicto de demostración después de preparar las unidades")
    monkeypatch.setattr(service, "_core_rows", conflict)
    migration_jobs.run(UUID(project["id"]))
    result = read(suite, project)
    job = result["simulation"]["job"]
    assert job["state"] == "failed" and not result["simulation"]["can_commit"]
    assert job["progress"]["objects"]["units"]["state"] == "rolled_back"
    assert job["progress"]["objects"]["products"]["state"] == "error"
    assert job["progress"]["percent"] < 100
    assert count(suite[1], UnitOfMeasure) == count(suite[1], Product) == 0


def test_live_snapshots_are_scoped_by_tenant_project_and_job_and_removed_on_exit():
    staged = [SimpleNamespace(object_code="employees", rows=[{}, {}, {}, {}])]
    tenant, project, job = uuid4(), uuid4(), uuid4()
    barrier = Barrier(2)
    def execute(other_job, rows):
        reporter = progress.Progress(tenant, project, other_job, staged)
        with progress.tracking(reporter):
            progress.start("employees"); progress.advance(validated=rows)
            barrier.wait(timeout=5)
            snapshot = progress.live_snapshot(tenant, project, other_job)
            assert snapshot["objects"]["employees"]["validated_rows"] == rows
            snapshot["objects"]["employees"]["validated_rows"] = 99
            assert progress.live_snapshot(tenant, project, other_job)["objects"]["employees"]["validated_rows"] == rows
            assert progress.live_snapshot(uuid4(), project, other_job) is None
            assert progress.live_snapshot(tenant, uuid4(), other_job) is None
            return reporter.data["attempt_id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(execute, job, 1)
        two = pool.submit(execute, uuid4(), 3)
        assert one.result() != two.result()
    assert progress.live_snapshot(tenant, project, job) is None
    # A restarted attempt starts at zero; old numbers cannot authorize a commit.
    fresh = progress.Progress(tenant, project, job, staged)
    assert fresh.snapshot()["percent"] == 0


def test_project_payload_uses_only_current_live_attempt_and_handles_legacy_jobs(suite, monkeypatch):
    setup_worker(monkeypatch, suite)
    project = upload(suite, create(suite, ["base"]), "units")
    queued = enqueue(suite, project).json()
    assert queued["simulation"]["job"]["state"] == "queued"
    assert queued["simulation"]["job"]["progress"] is None
    db = suite[1]
    stored = db.get(MigrationProject, UUID(project["id"]))
    reporter = progress.Progress(stored.tenant_id, stored.id, stored.simulation["job"]["id"], service.items(db, stored))
    with progress.tracking(reporter):
        progress.start("units"); progress.advance(validated=1)
        payload = service.payload(db, stored)
        assert payload["simulation"]["job"]["progress"]["objects"]["units"]["percent"] == 50
        assert "progress" not in stored.simulation["job"]
    assert service.payload(db, stored)["simulation"]["job"]["progress"] is None
