from datetime import date, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.routes.operations import DiscountPolicyIn, PriceListIn, PriceListItemIn, router


def test_versioned_commercial_forms_reject_inverted_validity():
    tomorrow = date.today() + timedelta(days=1)
    yesterday = date.today() - timedelta(days=1)
    with pytest.raises(ValidationError):
        PriceListIn(code="LISTA", name="Lista", currency_id=uuid4(), valid_from=tomorrow, valid_to=yesterday)
    with pytest.raises(ValidationError):
        PriceListItemIn(product_id=uuid4(), unit_price="12.50", valid_from=tomorrow, valid_to=yesterday)
    with pytest.raises(ValidationError):
        DiscountPolicyIn(code="DESC", name="Descuento", max_discount_percent="10", valid_from=tomorrow, valid_to=yesterday)


def test_specialized_configuration_routes_are_registered_before_generic_catalog_route():
    paths = [route.path for route in router.routes]
    generic_index = paths.index("/operations/configuration/{resource}")
    assert paths.index("/operations/configuration/exchange-rates") < generic_index
    assert paths.index("/operations/configuration/price-lists") < generic_index
    assert paths.index("/operations/configuration/discount-policies") < generic_index
