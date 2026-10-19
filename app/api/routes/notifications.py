from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.security.auth import Actor, ROLE_PERMISSIONS, get_actor


router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationOut(BaseModel):
    id: str
    category: str
    severity: str
    title: str
    body: str
    module: str
    entity_id: UUID | None = None
    created_at: datetime
    is_read: bool = False


def _can(actor: Actor, permission: str) -> bool:
    granted = ROLE_PERMISSIONS.get(actor.role_code, set())
    return "*" in granted or permission in granted


def _money(value: Decimal) -> str:
    return f"S/ {value:,.2f}"


def _current_alerts(actor: Actor, db: Session) -> list[NotificationOut]:
    """Return current, actionable alerts limited to modules the member can access.

    Alerts are deliberately calculated from the source records instead of copied into
    a second table. When the source condition is resolved, the alert disappears too.
    This is the base event feed that future email/push channels can consume.
    """
    tenant = str(actor.tenant_id)
    now = datetime.now(timezone.utc)
    alerts: list[NotificationOut] = []

    if _can(actor, "inventory:read"):
        rows = db.execute(
            text("""
                select p.id, p.sku, p.name, p.minimum_stock,
                       coalesce(sum(i.quantity), 0) as current_stock
                from products p
                left join inventories i on i.product_id = p.id and i.tenant_id = p.tenant_id
                where p.tenant_id = :tenant and p.is_active = true
                group by p.id, p.sku, p.name, p.minimum_stock
                having coalesce(sum(i.quantity), 0) <= p.minimum_stock
                order by current_stock asc, p.name asc
                limit 20
            """),
            {"tenant": tenant},
        ).mappings()
        for row in rows:
            current = Decimal(row["current_stock"])
            minimum = Decimal(row["minimum_stock"])
            severity = "critical" if current <= 0 else "warning"
            alerts.append(NotificationOut(
                id=f"stock-low:{row['id']}", category="inventory", severity=severity,
                title="Stock agotado" if current <= 0 else "Stock bajo",
                body=f"{row['name']} ({row['sku']}) tiene {current:g} unidades; mínimo configurado: {minimum:g}.",
                module="Inventario", entity_id=row["id"], created_at=now,
            ))

    if _can(actor, "accounts:read"):
        receivable = db.execute(text("""
            select count(*) as count, coalesce(sum(balance_amount), 0) as total
            from accounts_receivable
            where tenant_id = :tenant and status in ('open', 'partial') and balance_amount > 0
        """), {"tenant": tenant}).mappings().one()
        if receivable["count"]:
            alerts.append(NotificationOut(
                id="receivables-pending", category="finance", severity="warning",
                title="Cobros pendientes", module="Finanzas",
                body=f"Hay {receivable['count']} cuenta(s) por cobrar por {_money(Decimal(receivable['total']))}.",
                created_at=now,
            ))
        payable = db.execute(text("""
            select count(*) as count, coalesce(sum(balance_amount), 0) as total
            from accounts_payable
            where tenant_id = :tenant and status in ('open', 'partial') and balance_amount > 0
        """), {"tenant": tenant}).mappings().one()
        if payable["count"]:
            alerts.append(NotificationOut(
                id="payables-pending", category="finance", severity="warning",
                title="Pagos pendientes", module="Finanzas",
                body=f"Hay {payable['count']} cuenta(s) por pagar por {_money(Decimal(payable['total']))}.",
                created_at=now,
            ))

    if _can(actor, "treasury:read"):
        unmatched = db.execute(text("""
            select count(*) as count from bank_statement_lines
            where tenant_id = :tenant and reconciled = false
        """), {"tenant": tenant}).scalar_one()
        if unmatched:
            alerts.append(NotificationOut(
                id="bank-reconciliation-pending", category="treasury", severity="info",
                title="Conciliación pendiente", module="Conciliación",
                body=f"Hay {unmatched} partida(s) bancaria(s) pendiente(s) de conciliar.",
                created_at=now,
            ))

    for table, module, permission, label in (
        ("purchase_orders", "Compras", "purchases:read", "orden(es) de compra"),
        ("sales_quotes", "Cotizaciones", "sales:read", "cotización(es)"),
        ("sales_orders", "Pedidos de venta", "sales:read", "pedido(s) de venta"),
    ):
        if not _can(actor, permission):
            continue
        count = db.execute(
            text(f"select count(*) from {table} where tenant_id = :tenant and status = 'draft'"),
            {"tenant": tenant},
        ).scalar_one()
        if count:
            alerts.append(NotificationOut(
                id=f"{table}-review", category="workflow", severity="info", module=module,
                title="Documentos por revisar",
                body=f"Hay {count} {label} en borrador que requieren revisión o aprobación.",
                created_at=now,
            ))

    rank = {"critical": 0, "warning": 1, "info": 2}
    return sorted(alerts, key=lambda alert: (rank[alert.severity], alert.title))


def _read_notification_ids(actor: Actor, db: Session) -> set[str]:
    return set(db.execute(
        text("""
            select notification_id from notification_read_states
            where tenant_id = :tenant and user_id = :user
        """),
        {"tenant": str(actor.tenant_id), "user": str(actor.user_id)},
    ).scalars())


@router.get("", response_model=list[NotificationOut])
def list_notifications(actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    """Return actionable alerts, preserving each member's read state."""
    alerts = _current_alerts(actor, db)
    read_ids = _read_notification_ids(actor, db)
    for alert in alerts:
        alert.is_read = alert.id in read_ids
    return alerts


def _mark_as_read(notification: NotificationOut, actor: Actor, db: Session) -> NotificationOut:
    # Notifications are dynamic, so their identity is validated against the
    # current source data before it is persisted as read.
    db.execute(
        text("""
            insert into notification_read_states (tenant_id, user_id, notification_id)
            values (:tenant, :user, :notification_id)
            on conflict (tenant_id, user_id, notification_id)
            do update set read_at = excluded.read_at
        """),
        {
            "tenant": str(actor.tenant_id),
            "user": str(actor.user_id),
            "notification_id": notification.id,
        },
    )
    notification.is_read = True
    return notification


@router.post("/read-all", response_model=list[NotificationOut])
def mark_all_notifications_as_read(actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    alerts = _current_alerts(actor, db)
    for alert in alerts:
        _mark_as_read(alert, actor, db)
    db.commit()
    return alerts


@router.post("/{notification_id}/read", response_model=NotificationOut)
def mark_notification_as_read(notification_id: str, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    alerts = {alert.id: alert for alert in _current_alerts(actor, db)}
    notification = alerts.get(notification_id)
    if notification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="La notificación no existe o ya no requiere atención.",
        )
    _mark_as_read(notification, actor, db)
    db.commit()
    return notification
