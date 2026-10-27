"""Shared pagination contract for collection endpoints.

The API keeps the resource name at the top level so consumers can extend an
endpoint without breaking its payload shape, for example::

    {"products": [...], "meta": {"page": 1, "page_size": 25, ...}}
"""

from math import ceil
from typing import Any

from sqlalchemy import Select, TextClause, func, select
from sqlalchemy.orm import Session


def paginate(
    db: Session,
    statement: Select[Any],
    *,
    key: str,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    """Execute a stable offset page and return records plus transport metadata."""
    total = db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
    total_pages = ceil(total / page_size) if total else 0
    # A client may request a stale page after a deletion. Returning an empty
    # page with accurate metadata is deterministic and lets the UI step back.
    items = db.scalars(statement.offset((page - 1) * page_size).limit(page_size)).all()
    return {
        key: items,
        "meta": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "has_previous": page > 1,
            "has_next": page < total_pages,
        },
    }


def paginate_mappings(
    db: Session,
    *,
    rows_statement: TextClause,
    count_statement: TextClause,
    params: dict[str, Any],
    key: str,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    """Paginate tenant-scoped textual reports while preserving mapping rows."""
    total = db.scalar(count_statement.params(**params)) or 0
    total_pages = ceil(total / page_size) if total else 0
    items = db.execute(
        rows_statement,
        {**params, "limit": page_size, "offset": (page - 1) * page_size},
    ).mappings().all()
    return {
        key: items,
        "meta": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "has_previous": page > 1,
            "has_next": page < total_pages,
        },
    }
