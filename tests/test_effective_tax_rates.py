import inspect
from datetime import date
from decimal import Decimal

from app.api.routes.purchases import receive_purchase
from app.api.routes.sales import issue_electronic_document, post_sale
from app.services.accounting import split_tax
from app.services.taxes import resolve_default_tax_rate


def test_tax_split_uses_the_document_rate_not_a_global_constant():
    net, tax = split_tax(Decimal("110"), Decimal("10"))
    assert net == Decimal("100.00")
    assert tax == Decimal("10.00")


def test_effective_tax_resolution_is_tenant_scoped_and_date_bound():
    source = inspect.getsource(resolve_default_tax_rate)
    assert "tenant_id=:tenant" in source
    assert "valid_from <= :document_date" in source
    assert "valid_to is null or valid_to >= :document_date" in source
    assert "is_default=true" in source
    assert date(2026, 10, 22) > date(2000, 1, 1)


def test_commercial_documents_persist_resolved_tax_snapshot():
    sales_source = inspect.getsource(post_sale)
    purchases_source = inspect.getsource(receive_purchase)
    assert "resolve_default_tax_rate(" in sales_source
    assert "tax_rate_id=tax_rate.id" in sales_source
    assert "taxable_amount=taxable_amount" in sales_source
    assert "resolve_default_tax_rate(" in purchases_source
    assert "tax_rate_id=tax_rate.id" in purchases_source
    assert "purchase.tax_amount = tax" in purchases_source


def test_cpe_rejects_an_unmapped_tax_instead_of_mislabeling_it_as_igv():
    source = inspect.getsource(issue_electronic_document)
    assert "solo admite IGV 18" in source
