import pytest

from app.services.handwritten_signatures import approval_is_signed, validate_handwritten_signature_svg
from app.api.routes.operations import physical_count_approval_payload


VALID_SIGNATURE = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 220"><path d="M 10 20 L 30 40"/></svg>'


def test_accepts_a_non_empty_handwritten_svg_path():
    assert validate_handwritten_signature_svg(VALID_SIGNATURE) == VALID_SIGNATURE


def test_rejects_empty_or_executable_handwritten_svg():
    with pytest.raises(ValueError, match="trazo"):
        validate_handwritten_signature_svg('<svg xmlns="http://www.w3.org/2000/svg"></svg>')
    with pytest.raises(ValueError, match="seguro"):
        validate_handwritten_signature_svg('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><path d="M 1 1"/></svg>')


def test_rejects_non_path_svg_content():
    with pytest.raises(ValueError, match="solo puede contener trazos"):
        validate_handwritten_signature_svg('<svg xmlns="http://www.w3.org/2000/svg"><path d="M 1 1 L 2 2"/><circle cx="5" cy="5" r="4" /></svg>')


def test_physical_count_requires_approved_cryptographic_signature():
    assert not approval_is_signed(None)
    assert not approval_is_signed({"status": "approved", "signature_id": None})
    assert not approval_is_signed({"status": "pending", "signature_id": "signature"})
    assert approval_is_signed({"status": "approved", "signature_id": "signature"})


def test_physical_count_approval_payload_is_serializable_json():
    """Submitting a count must not use an untyped value in PostgreSQL jsonb builders."""
    assert physical_count_approval_payload("INVF-2026-001") == '{"number":"INVF-2026-001"}'
