from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.routes import notifications
from app.security.auth import Actor


class CaptureDb:
    def __init__(self):
        self.executions = []
        self.committed = False

    def execute(self, statement, values):
        self.executions.append((str(statement), values))

    def commit(self):
        self.committed = True


def test_unknown_notification_cannot_be_marked_as_read(monkeypatch):
    actor = Actor(user_id=uuid4(), tenant_id=uuid4(), role_code="inventory")
    db = CaptureDb()
    monkeypatch.setattr(notifications, "_current_alerts", lambda *_: [])

    with pytest.raises(HTTPException) as error:
        notifications.mark_notification_as_read("forged-alert", actor, db)

    assert error.value.status_code == 404
    assert not db.executions
    assert not db.committed


def test_marking_known_notification_scopes_state_to_the_current_member(monkeypatch):
    actor = Actor(user_id=uuid4(), tenant_id=uuid4(), role_code="inventory")
    db = CaptureDb()
    alert = notifications.NotificationOut(
        id="stock-low:sample", category="inventory", severity="warning",
        title="Stock bajo", body="Producto con stock bajo", module="Inventario",
        created_at=notifications.datetime.now(notifications.timezone.utc),
    )
    monkeypatch.setattr(notifications, "_current_alerts", lambda *_: [alert])

    result = notifications.mark_notification_as_read(alert.id, actor, db)

    assert result.is_read is True
    assert db.committed is True
    assert db.executions[0][1] == {
        "tenant": str(actor.tenant_id), "user": str(actor.user_id), "notification_id": alert.id,
    }
