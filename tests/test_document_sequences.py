import uuid
from unittest.mock import Mock

from app.schemas.transactions import PurchaseCreate, SaleCreate
from app.services.document_sequences import allocate_document_number, format_document_number


def test_operational_folio_is_fixed_width_and_uses_normalized_prefix():
    assert format_document_number(" v ", 7) == "V-00000007"


def test_sale_and_purchase_accept_legacy_client_without_folio():
    # The API now assigns the document folio server-side. Schema acceptance
    # prevents old forms/clients from breaking while they are rolled out.
    assert SaleCreate.model_fields["number"].is_required() is False
    assert PurchaseCreate.model_fields["number"].is_required() is False


def test_legacy_sequence_uses_legacy_unique_index_columns():
    """Un documento sin entidad/sucursal debe coincidir con su índice parcial.

    Los tenants históricos no tienen contexto de entidad/sucursal y PostgreSQL
    solo puede inferir ``ux_document_sequences_legacy_scope`` si el objetivo
    de ``ON CONFLICT`` usa exactamente sus tres columnas.
    """
    db = Mock()
    sequence = Mock(prefix="V", next_number=1, is_active=True)
    db.scalar.return_value = sequence

    allocate_document_number(
        db,
        tenant_id=uuid.uuid4(),
        module="sales",
        document_type="sale",
        legal_entity_id=None,
        branch_id=None,
    )

    statement = db.execute.call_args.args[0]
    compiled = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "ON CONFLICT (tenant_id, module, document_type)" in compiled
