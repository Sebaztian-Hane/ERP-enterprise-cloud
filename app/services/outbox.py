"""Transactional outbox writes shared by asynchronous integrations."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def enqueue(db: Session, *, tenant_id: UUID, topic: str, payload: dict[str, Any]) -> None:
    """Insert an event in the caller's open business transaction."""
    db.execute(
        text("""
            insert into outbox_events(tenant_id, topic, payload)
            values (:tenant_id, :topic, cast(:payload as jsonb))
        """),
        {
            "tenant_id": str(tenant_id),
            "topic": topic,
            "payload": json.dumps(payload, separators=(",", ":")),
        },
    )
