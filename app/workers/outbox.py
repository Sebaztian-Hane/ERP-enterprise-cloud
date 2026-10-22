"""PostgreSQL transactional-outbox worker.

Run this process separately from FastAPI. It leases events with SKIP LOCKED,
delivers them at least once and applies bounded exponential retries. Consumers
must use the included event id to deduplicate an occasional delivery replay.
"""

from __future__ import annotations

import logging
import signal
import time
from threading import Event, Thread
from typing import Any

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.rag_sync import dispatch_sync_event


logger = logging.getLogger(__name__)
STOP = Event()
POLL_SECONDS = 2
BATCH_SIZE = 20
MAX_ATTEMPTS = 10
LEASE_SECONDS = 300


def retry_delay_seconds(attempts: int) -> int:
    """Bound exponential backoff: 2s, 4s … up to five minutes."""
    return min(300, 2 ** max(1, attempts))


def claim_batch() -> list[dict[str, Any]]:
    """Lease currently available events without allowing worker races."""
    with SessionLocal() as db:
        # A process can be interrupted after claiming a row and before it can
        # persist the delivery result. Releasing only expired leases ensures
        # those events are retried while an active worker keeps its lease.
        db.execute(
            text("""
                update outbox_events
                set status = 'pending', locked_at = null,
                    available_at = now(),
                    last_error = coalesce(last_error, 'Entrega interrumpida; evento recuperado')
                where status = 'processing'
                  and locked_at < now() - (:lease_seconds * interval '1 second')
            """),
            {"lease_seconds": LEASE_SECONDS},
        )
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


def run_until_stopped(stop_event: Event, *, worker_name: str) -> None:
    """Process the durable queue until shutdown, surviving transient failures."""
    logger.info("outbox_worker_started worker=%s", worker_name)
    while not stop_event.is_set():
        try:
            processed = process_batch()
        except Exception:  # noqa: BLE001 - DB/network failures must not kill retries
            logger.exception("outbox_poll_failed worker=%s", worker_name)
            processed = 0
        if not processed:
            stop_event.wait(POLL_SECONDS)
    logger.info("outbox_worker_stopped worker=%s", worker_name)


def start_embedded_worker() -> tuple[Event, Thread]:
    """Start the no-extra-cost API-hosted consumer used on Render Free.

    The same lease SQL used by the standalone process protects against two API
    instances temporarily running at once during a deploy.
    """
    stop_event = Event()
    thread = Thread(
        target=run_until_stopped,
        args=(stop_event,),
        kwargs={"worker_name": "embedded-api"},
        name="outbox-embedded-worker",
        daemon=True,
    )
    thread.start()
    return stop_event, thread


def _request_stop(*_args: object) -> None:
    STOP.set()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    run_until_stopped(STOP, worker_name="standalone")


if __name__ == "__main__":
    main()
