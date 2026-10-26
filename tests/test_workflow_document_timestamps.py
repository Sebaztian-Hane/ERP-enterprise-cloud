from pathlib import Path


def test_workflow_documents_receive_an_updated_at_migration():
    """Status transitions write timestamps and require these columns in PostgreSQL."""
    migration = Path("alembic/versions/0060_workflow_document_timestamps.sql")

    assert migration.exists()
    sql = migration.read_text(encoding="utf-8")
    for table in ("sales_quotes", "sales_orders", "purchase_orders"):
        assert f"alter table public.{table}" in sql
        assert "add column if not exists updated_at timestamptz" in sql
