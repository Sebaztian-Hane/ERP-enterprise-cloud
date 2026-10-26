from app.services.cpe_evidence import canonical_json_bytes, content_hash, redact_provider_data


def test_provider_evidence_hash_is_stable_and_tokens_are_redacted():
    response = {
        "documentId": "cpe-123",
        "personaToken": "DEV_never_store_this",
        "nested": {"api_key": "also-secret", "status": "ACEPTADO"},
    }

    safe = redact_provider_data(response)

    assert safe["personaToken"] == "[REDACTED]"
    assert safe["nested"]["api_key"] == "[REDACTED]"
    assert content_hash(canonical_json_bytes({"b": 2, "a": 1})) == content_hash(canonical_json_bytes({"a": 1, "b": 2}))


def test_cpe_evidence_migration_is_append_only_and_repairs_provider_statuses():
    migration = open("alembic/versions/0059_cpe_artifact_traceability.sql", encoding="utf-8").read()

    assert "'reserved'" in migration
    assert "'submitted'" in migration
    assert "electronic_document_artifacts" in migration
    assert "electronic_document_events" in migration
    assert "before update or delete" in migration
    assert "enable row level security" in migration
