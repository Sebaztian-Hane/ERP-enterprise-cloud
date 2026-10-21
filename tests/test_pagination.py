from types import SimpleNamespace

from sqlalchemy import column, select

from app.schemas.pagination import paginate


class PageSession:
    def __init__(self):
        self.statement = None

    def scalar(self, statement):
        return 42

    def scalars(self, statement):
        self.statement = statement
        return SimpleNamespace(all=lambda: ["record-26"])


def test_paginate_returns_named_items_and_complete_metadata():
    db = PageSession()
    result = paginate(db, select(column("id")), key="products", page=2, page_size=25)

    assert result["products"] == ["record-26"]
    assert result["meta"] == {
        "page": 2,
        "page_size": 25,
        "total": 42,
        "total_pages": 2,
        "has_previous": True,
        "has_next": False,
    }
