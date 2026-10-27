from types import SimpleNamespace

from sqlalchemy import column, select, text

from app.schemas.pagination import paginate, paginate_mappings


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


def test_paginate_mappings_returns_rows_with_metadata():
    class MappingSession:
        def scalar(self, statement):
            return 26

        def execute(self, statement, params):
            assert params["limit"] == 25
            assert params["offset"] == 25
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: [{"number": "PV-026"}]))

    result = paginate_mappings(
        MappingSession(),
        rows_statement=text("select * from documents limit :limit offset :offset"),
        count_statement=text("select count(*) from documents"),
        params={},
        key="documents",
        page=2,
        page_size=25,
    )

    assert result["documents"] == [{"number": "PV-026"}]
    assert result["meta"]["total"] == 26
    assert result["meta"]["has_previous"] is True
    assert result["meta"]["has_next"] is False
