import pytest
from pydantic import ValidationError

from app.schemas.legal_entities import LegalEntityCreate


def test_legal_entity_requires_peruvian_ruc_and_normalizes_text():
    entity = LegalEntityCreate(
        code="  EC-PE  ",
        legal_name="  EnterpriseCloud Perú S.A.C.  ",
        tax_id="20123456789",
        fiscal_address="  Av. Principal 123, Lima  ",
    )

    assert entity.code == "EC-PE"
    assert entity.legal_name == "EnterpriseCloud Perú S.A.C."
    assert entity.fiscal_address == "Av. Principal 123, Lima"


def test_legal_entity_rejects_invalid_ruc():
    with pytest.raises(ValidationError):
        LegalEntityCreate(code="EC", legal_name="Empresa", tax_id="12345678", fiscal_address="Av. Lima 123")
