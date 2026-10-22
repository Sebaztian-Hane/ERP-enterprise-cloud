from app.schemas.transactions import PurchaseCreate, SaleCreate
from app.services.document_sequences import format_document_number


def test_operational_folio_is_fixed_width_and_uses_normalized_prefix():
    assert format_document_number(" v ", 7) == "V-00000007"


def test_sale_and_purchase_accept_legacy_client_without_folio():
    # The API now assigns the document folio server-side. Schema acceptance
    # prevents old forms/clients from breaking while they are rolled out.
    assert SaleCreate.model_fields["number"].is_required() is False
    assert PurchaseCreate.model_fields["number"].is_required() is False
