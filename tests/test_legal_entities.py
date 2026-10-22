import pytest
from pydantic import ValidationError

from app.schemas.legal_entities import BranchUpdate, LegalEntityCreate, LegalEntityUpdate


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


def test_legal_entity_update_preserves_optional_fields_and_normalizes_text():
    update = LegalEntityUpdate(legal_name="  Nueva razón social  ", is_active=False)

    assert update.legal_name == "Nueva razón social"
    assert update.is_active is False


def test_branch_update_validates_ubigeo_when_provided():
    with pytest.raises(ValidationError):
        BranchUpdate(ubigeo="1501")
