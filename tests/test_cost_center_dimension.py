import inspect

from app.api.routes.purchases import list_purchase_cost_centers, receive_purchase
from app.api.routes.sales import list_sale_cost_centers, post_sale
from app.services.accounting import post_entry
from app.services.cost_centers import resolve_cost_center


def test_cost_center_resolution_is_active_and_tenant_scoped():
    source = inspect.getsource(resolve_cost_center)
    assert "tenant_id=:tenant" in source
    assert "is_active=true" in source
    assert "code='GENERAL'" in source


def test_commercial_forms_only_receive_active_tenant_cost_centers():
    assert "where tenant_id=:tenant and is_active=true" in inspect.getsource(list_sale_cost_centers)
    assert "where tenant_id=:tenant and is_active=true" in inspect.getsource(list_purchase_cost_centers)


def test_postings_propagate_the_document_cost_center_to_every_line():
    sales_source = inspect.getsource(post_sale)
    purchases_source = inspect.getsource(receive_purchase)
    accounting_source = inspect.getsource(post_entry)
    assert "cost_center_id=cost_center.id" in sales_source
    assert "cost_center_id=cost_center.id" in purchases_source
    assert "cost_center_id, debit, credit" in accounting_source
    assert "cost_center_id = line[3]" in accounting_source
