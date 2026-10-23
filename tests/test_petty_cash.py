from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_petty_cash_migration_is_tenant_scoped_and_protected_by_rls():
    sql = (ROOT / "alembic" / "versions" / "0049_petty_cash.sql").read_text(encoding="utf-8")
    for table in ("petty_cash_funds", "petty_cash_expenses", "petty_cash_replenishments"):
        assert f"create table public.{table}" in sql
        assert "tenant_id uuid not null" in sql
        assert f"alter table public.{table} enable row level security" in sql
        assert f"create policy {table}_tenant" in sql


def test_petty_cash_route_requires_approval_and_separate_execution():
    source = (ROOT / "app" / "api" / "routes" / "treasury.py").read_text(encoding="utf-8")
    assert "create_petty_cash_evidence" in source
    assert "status='approved'" in source
    assert "La separación de funciones exige otro ejecutor" in source
    assert "post_entry(" in source


def test_default_chart_has_petty_cash_and_expense_accounts():
    source = (ROOT / "app" / "services" / "accounting.py").read_text(encoding="utf-8")
    assert '("1020", "Caja chica", "asset")' in source
    assert '("6590", "Otros gastos de gestión", "expense")' in source
