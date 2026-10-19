from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.routes import notifications
from app.api.routes.payments import _same_payment_request
from app.schemas.payments import PaymentCreate
from app.security.auth import Actor
from app.services import audit


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


def test_payment_retry_must_match_the_original_financial_data():
    account_id, customer_id = uuid4(), uuid4()
    data = PaymentCreate(
        number="COB-001", payment_type="receipt", amount="25.00", method="transferencia",
        cash_account_id=account_id, customer_id=customer_id,
        allocations=[{"receivable_id": str(uuid4()), "amount": "25.00"}],
    )

    class Existing:
        payment_type = "receipt"; amount = data.amount; method = "transferencia"
        cash_account_id = account_id; supplier_id = None; reference = None; note = None

    Existing.customer_id = customer_id

    assert _same_payment_request(Existing(), data)
    Existing.amount = data.amount + 1
    assert not _same_payment_request(Existing(), data)


def test_audit_record_accepts_optional_detail(monkeypatch):
    class AuditDb:
        def __init__(self):
            self.items = []

        def add(self, item):
            self.items.append(item)

    monkeypatch.setattr(audit, "queue_sync_event", lambda *_args, **_kwargs: None)
    db = AuditDb()
    audit.record(
        db,
        tenant_id=uuid4(),
        actor_user_id=uuid4(),
        action="create_from_sale",
        module="customers",
        entity="customer",
        detail="Cliente creado durante venta",
    )

    assert db.items[0].detail == "Cliente creado durante venta"
