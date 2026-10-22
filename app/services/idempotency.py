"""Persistent, tenant-scoped idempotency for commands with business effects.

The key is intentionally scoped to the authenticated actor and operation.  A
client retry can retrieve its original result, while a reused key with a new
payload is rejected rather than applying a second business operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session


MIN_KEY_LENGTH = 8
MAX_KEY_LENGTH = 200


@dataclass(frozen=True)
class IdempotencyClaim:
    """A newly claimed command, or a completed command ready for replay."""

    id: str | None = None
    response_status: int | None = None
    response_body: dict[str, Any] | list[Any] | None = None

    @property
    def is_replay(self) -> bool:
        return self.response_status is not None and self.response_body is not None


def canonical_hash(payload: Any) -> str:
    """Hash normalized JSON so key ordering cannot change the command identity."""
    encoded = json.dumps(
        jsonable_encoder(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def claim(
    db: Session,
    *,
    tenant_id: UUID,
    actor_user_id: UUID,
    operation: str,
    key: str | None,
    payload: Any,
) -> IdempotencyClaim | None:
    """Atomically claim a key or return the response of a completed retry.

    PostgreSQL's `ON CONFLICT` waits for a concurrent owner of the same unique
    key.  It avoids the classic select-then-insert race without creating a
    second sale, payment or other command.
    """
    if key is None:
        return None
    normalized_key = key.strip()
    if not MIN_KEY_LENGTH <= len(normalized_key) <= MAX_KEY_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Idempotency-Key debe tener entre {MIN_KEY_LENGTH} y {MAX_KEY_LENGTH} caracteres",
        )

    digest = canonical_hash(payload)
    inserted = db.execute(
        text("""
            insert into idempotency_requests
                (tenant_id, actor_user_id, operation, idempotency_key, request_hash)
            values (:tenant_id, :actor_user_id, :operation, :idempotency_key, :request_hash)
            on conflict (tenant_id, actor_user_id, operation, idempotency_key) do nothing
            returning id
        """),
        {
            "tenant_id": str(tenant_id),
            "actor_user_id": str(actor_user_id),
            "operation": operation,
            "idempotency_key": normalized_key,
            "request_hash": digest,
        },
    ).scalar_one_or_none()
    if inserted:
        return IdempotencyClaim(id=str(inserted))

    existing = db.execute(
        text("""
            select id, request_hash, status, response_status, response_body
            from idempotency_requests
            where tenant_id = :tenant_id
              and actor_user_id = :actor_user_id
              and operation = :operation
              and idempotency_key = :idempotency_key
            for update
        """),
        {
            "tenant_id": str(tenant_id),
            "actor_user_id": str(actor_user_id),
            "operation": operation,
            "idempotency_key": normalized_key,
        },
    ).mappings().one()
    if existing["request_hash"] != digest:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency-Key ya fue usada con una solicitud diferente",
        )
    if existing["status"] != "completed" or existing["response_body"] is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La solicitud con esta Idempotency-Key aún está en proceso; vuelve a intentarlo",
        )
    return IdempotencyClaim(
        id=str(existing["id"]),
        response_status=int(existing["response_status"]),
        response_body=existing["response_body"],
    )


def complete(
    db: Session,
    *,
    claim: IdempotencyClaim | None,
    response_body: Any,
    response_status: int,
) -> None:
    """Persist the original response in the same transaction as the command."""
    if claim is None:
        return
    if claim.is_replay or claim.id is None:
        raise RuntimeError("Solo una reclamación nueva puede completarse")
    db.execute(
        text("""
            update idempotency_requests
            set status = 'completed', response_status = :response_status,
                response_body = cast(:response_body as jsonb), completed_at = now()
            where id = :id and status = 'processing'
        """),
        {
            "id": claim.id,
            "response_status": response_status,
            "response_body": json.dumps(jsonable_encoder(response_body), separators=(",", ":")),
        },
    )


def replay_response(claim: IdempotencyClaim) -> JSONResponse:
    """Return the exact successful response without rerunning business logic."""
    if not claim.is_replay:
        raise RuntimeError("La reclamación no contiene una respuesta para reproducir")
    return JSONResponse(
        content=claim.response_body,
        status_code=claim.response_status,
        headers={"Idempotency-Replayed": "true"},
    )
