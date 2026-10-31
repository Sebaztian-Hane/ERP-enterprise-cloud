from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_approval_governance_migration_keeps_evidence_and_delegations_server_mediated():
    migration = (ROOT / "alembic/versions/0057_approval_governance_and_evidence.sql").read_text(encoding="utf-8")

    assert "create table if not exists public.approval_delegations" in migration
    assert "approval_delegations_server_only" in migration
    assert "create table if not exists public.approval_document_attachments" in migration
    assert "approval_documents_immutable" in migration
    assert "approval_signatures_append_only" in migration
    assert "signature_challenge_id" in migration


def test_approval_decisions_and_verification_preserve_signature_provenance():
    operations = (ROOT / "app/api/routes/operations.py").read_text(encoding="utf-8")
    documents = (ROOT / "app/api/routes/documents.py").read_text(encoding="utf-8")
    evidence = (ROOT / "app/services/approval_documents.py").read_text(encoding="utf-8")

    assert "_delegated_from_for_request" in operations
    assert "approve_delegated" in operations
    assert "attach_approval_document" in operations
    assert "signature_challenge_id" in evidence
    assert "verify_approval_record" in documents
    assert "verify_p256_signature" in documents


def test_delegated_approval_lock_does_not_lock_nullable_policy_join():
    """PostgreSQL rejects `FOR UPDATE` across a nullable LEFT JOIN side."""
    operations = (ROOT / "app/api/routes/operations.py").read_text(encoding="utf-8")

    delegated_authorization = operations.split("def _delegated_from_for_request", 1)[1].split(
        "@router.post(\"/approvals/{request_id}/signature-challenge\")", 1
    )[0]
    assert "for update of r" in delegated_authorization.lower()
