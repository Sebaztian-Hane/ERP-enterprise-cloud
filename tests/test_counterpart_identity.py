import pytest
from pydantic import ValidationError

from app.schemas.catalog import CustomerCreate, CustomerUpdate, SupplierCreate


def test_customer_document_is_normalized_and_accepts_dni_or_ruc():
    assert CustomerCreate(code="CLI-1", name="Cliente", tax_id=" 74184767 ").tax_id == "74184767"
    assert CustomerUpdate(tax_id="20123456789").tax_id == "20123456789"


@pytest.mark.parametrize("tax_id", ["123", "ABCDEFGHI", "1234567890"])
def test_customer_document_rejects_invalid_identification(tax_id: str):
    with pytest.raises(ValidationError):
        CustomerCreate(code="CLI-1", name="Cliente", tax_id=tax_id)


def test_supplier_keeps_ruc_only_rule():
    with pytest.raises(ValidationError):
        SupplierCreate(code="PRV-1", name="Proveedor", tax_id="74184767")
