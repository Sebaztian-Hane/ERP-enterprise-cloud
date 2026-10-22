"""PostgreSQL transactional-outbox worker.

Run this process separately from FastAPI. It leases events with SKIP LOCKED,
delivers them at least once and applies bounded exponential retries. Consumers
must use the included event id to deduplicate an occasional delivery replay.
"""

from __future__ import annotations

import logging
import signal
import time
from threading import Event
from typing import Any

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.rag_sync import dispatch_sync_event


logger = logging.getLogger(__name__)
STOP = Event()
POLL_SECONDS = 2
BATCH_SIZE = 20
MAX_ATTEMPTS = 10


def retry_delay_seconds(attempts: int) -> int:
    """Bound exponential backoff: 2s, 4s … up to five minutes."""
    return min(300, 2 ** max(1, attempts))


def claim_batch() -> list[dict[str, Any]]:
    """Lease currently available events without allowing worker races."""
    with SessionLocal() as db:
        rows = db.execute(
            text("""
                with candidates as (
                  select id
                  from outbox_events
                  where status = 'pending' and available_at <= now()
                  order by created_at, id
                  for update skip locked
                  limit :batch_size
                )
                update outbox_events event
                set status = 'processing', locked_at = now()
                from candidates
                where event.id = candidates.id
                returning event.id, event.topic, event.payload, event.attempts
            """),
            {"batch_size": BATCH_SIZE},
        ).mappings().all()
        db.commit()
        return [dict(row) for row in rows]


def mark_delivered(event_id: str) -> None:
    with SessionLocal() as db:
        db.execute(
            text("""
                update outbox_events
                set status = 'delivered', delivered_at = now(), locked_at = null,
                    last_error = null
                where id = :id and status = 'processing'
            """),
            {"id": event_id},
        )
        db.commit()


def mark_failed(event_id: str, attempts: int, error: Exception) -> None:
    next_attempt = attempts + 1
    is_dead = next_attempt >= MAX_ATTEMPTS
    with SessionLocal() as db:
        db.execute(
            text("""
                update outbox_events
                set status = :status,
                    attempts = :attempts,
                    available_at = case when :is_dead then available_at
                      else now() + (:delay_seconds * interval '1 second') end,
                    locked_at = null,
                    last_error = :last_error
                where id = :id and status = 'processing'
            """),
            {
                "id": event_id,
                "status": "dead" if is_dead else "pending",
                "attempts": next_attempt,
                "is_dead": is_dead,
                "delay_seconds": retry_delay_seconds(next_attempt),
                "last_error": str(error)[:1000],
            },
        )
        db.commit()
    level = logging.ERROR if is_dead else logging.WARNING
    logger.log(level, "outbox_delivery_failed id=%s attempts=%s dead=%s", event_id, next_attempt, is_dead, exc_info=error)


def process_batch() -> int:
    events = claim_batch()
    for event in events:
        try:
            payload = dict(event["payload"])
            payload["event_id"] = str(event["id"])
            if event["topic"] != "rag.sync":
                raise RuntimeError(f"Tema de outbox no soportado: {event['topic']}")
            dispatch_sync_event(payload)
            mark_delivered(str(event["id"]))
        except Exception as exc:  # noqa: BLE001 - persist all delivery errors
            mark_failed(str(event["id"]), int(event["attempts"]), exc)
    return len(events)


def _request_stop(*_args: object) -> None:
    STOP.set()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    logger.info("outbox_worker_started")
    while not STOP.is_set():
        processed = process_batch()
        if not processed:
            STOP.wait(POLL_SECONDS)
    logger.info("outbox_worker_stopped")


if __name__ == "__main__":
    main()
