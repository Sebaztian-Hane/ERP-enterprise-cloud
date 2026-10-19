from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.api.routes.reports import activity_for_period


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _Session:
    def __init__(self):
        timestamp = lambda day: datetime(2026, 10, day, 12, tzinfo=timezone.utc)
        self.results = iter([
            [(timestamp(2), Decimal("100")), (timestamp(2), Decimal("50"))],
            [(timestamp(3), Decimal("40"))],
            [(timestamp(2), Decimal("75"))],
            [(timestamp(3), Decimal("10"))],
        ])

    def execute(self, _statement):
        return _Result(next(self.results))


def test_activity_series_fills_days_and_aggregates_each_operation_type():
    points = activity_for_period(_Session(), uuid4(), date(2026, 10, 1), date(2026, 10, 3))

    assert [point.date for point in points] == [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)]
    assert points[0].sales_total == Decimal("0")
    assert points[1].sales_total == Decimal("150")
    assert points[1].receipts_total == Decimal("75")
    assert points[2].purchases_total == Decimal("40")
    assert points[2].payments_total == Decimal("10")
