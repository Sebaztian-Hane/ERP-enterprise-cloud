"""Regression guards for object-ID endpoints across tenant boundaries.

These tests intentionally focus on operations where an attacker could replace
one UUID in a request path with an UUID belonging to another company.  The
database RLS layer is the second boundary; API queries must also remain scoped
when the application uses its server connection.
"""

import inspect

from app.api.routes.customers import update_customer
from app.api.routes.inventory import dispatch_transfer, receive_transfer
from app.api.routes.purchases import return_purchase
from app.api.routes.sales import return_sale
from app.api.routes.platform import get_tenant_apisunat_credentials
from app.api.routes.tenants import update_membership


def test_id_addressed_writes_keep_the_active_tenant_predicate():
    protected_handlers = [
        update_customer,
        dispatch_transfer,
        receive_transfer,
        return_purchase,
        return_sale,
        update_membership,
    ]
    for handler in protected_handlers:
        source = inspect.getsource(handler)
        assert "tenant_id" in source
        assert "actor.tenant_id" in source or "tenant_id=:tenant" in source


def test_server_only_tables_have_explicit_routing_boundary():
    """Sensitive provider credentials must never be returned by a route."""
    status_source = inspect.getsource(get_tenant_apisunat_credentials)
    assert "APISunatCredentialStatus" in status_source
    assert "persona_token" not in status_source.lower()
    assert "persona_id" not in status_source.lower()
