"""Advisory live counters; job intent and final results remain transactional.

The current deployment has one API process and a local thread pool. Live
counters are process-local and never drive locking, authorization or resuming
a job. A missing live snapshot is explicitly unknown, not fabricated progress.
Final snapshots are saved with the job result in the existing project row.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4

_current = ContextVar("migration_progress", default=None)
_lock = RLock()
_live = {}


def now():
    return datetime.now(timezone.utc).isoformat()


class Progress:
    def __init__(self, tenant_id, project_id, job_id, staged):
        self.key = (str(tenant_id), str(project_id), str(job_id))
        self.data = {
            "attempt_id": str(uuid4()), "phase": "running", "current_object": None,
            "started_at": now(), "updated_at": now(), "live": True,
            "objects": {item.object_code: {"total_rows": len(item.rows), "validated_rows": 0, "prepared_rows": 0, "percent": 0, "state": "pending", "phase": "waiting"} for item in staged},
        }

    def start(self, code):
        with _lock:
            self.data["current_object"] = code
            self.data["objects"][code].update(state="running", phase="validating")
            self.data["updated_at"] = now()

    def update(self, *, validated=0, prepared=0, phase=None):
        with _lock:
            item = self.data["objects"].get(self.data["current_object"])
            if item is None: return
            item["validated_rows"] = min(item["total_rows"], item["validated_rows"] + validated)
            item["prepared_rows"] = min(item["total_rows"], item["prepared_rows"] + prepared)
            if phase: item["phase"] = phase
            # Two actual units of work per input row: validate and prepare.
            # The object reaches 100 only after its database checks/flush pass.
            item["percent"] = min(99, (item["validated_rows"] + item["prepared_rows"]) * 100 // max(1, 2 * item["total_rows"]))
            self.data["updated_at"] = now()

    def finish_object(self, failed=False):
        with _lock:
            item = self.data["objects"][self.data["current_object"]]
            item.update(state="error" if failed else "processed", phase="done")
            if not failed: item.update(validated_rows=item["total_rows"], prepared_rows=item["total_rows"], percent=100)
            self.data["current_object"] = None
            self.data["updated_at"] = now()

    def finalizing(self):
        with _lock:
            self.data.update(phase="finalizing", current_object=None, updated_at=now())

    def snapshot(self, *, completed=False, failed=False, rolled_back=False):
        with _lock:
            result = deepcopy(self.data)
        total = sum(item["total_rows"] for item in result["objects"].values())
        done = sum(item["validated_rows"] + item["prepared_rows"] for item in result["objects"].values())
        result.update(total_rows=total, percent=100 if completed else min(99, done * 100 // max(1, 2 * total)))
        if completed or failed:
            result.update(phase="failed" if failed else "completed", current_object=None, live=False, updated_at=now())
        if failed:
            for item in result["objects"].values():
                if rolled_back and item["state"] in {"running", "processed"}: item["state"] = "rolled_back"
                elif item["state"] == "running": item["state"] = "error"
        return result


@contextmanager
def tracking(progress):
    token = _current.set(progress)
    with _lock: _live[progress.key] = progress
    try:
        yield progress
    finally:
        _current.reset(token)
        with _lock:
            if _live.get(progress.key) is progress: _live.pop(progress.key, None)


def live_snapshot(tenant_id, project_id, job_id):
    with _lock:
        progress = _live.get((str(tenant_id), str(project_id), str(job_id)))
        return progress.snapshot() if progress else None


def start(code):
    if progress := _current.get(): progress.start(code)


def advance(*, validated=0, prepared=0, phase=None):
    if progress := _current.get(): progress.update(validated=validated, prepared=prepared, phase=phase)


def finish(failed=False):
    if progress := _current.get(): progress.finish_object(failed)
