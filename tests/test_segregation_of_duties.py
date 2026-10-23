from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_approval_policy_defaults_to_executor_separation():
    migration = (ROOT / "alembic/versions/0048_approval_executor_separation.sql").read_text(encoding="utf-8")
    assert "require_executor_separation boolean not null default true" in migration


def test_sensitive_execution_checks_requester_and_approver():
    operations = (ROOT / "app/api/routes/operations.py").read_text(encoding="utf-8")
    workflow = (ROOT / "app/api/routes/workflow.py").read_text(encoding="utf-8")
    assert "La segregación de funciones exige que otra persona aplique el inventario físico" in operations
    assert "requested_by, r.decided_by" in operations
    assert "set status='approved', approved_by=:actor" in operations
    assert "La segregación de funciones exige que otra persona reciba la orden de compra" in workflow
    assert "coalesce(p.require_executor_separation, true)" in workflow
