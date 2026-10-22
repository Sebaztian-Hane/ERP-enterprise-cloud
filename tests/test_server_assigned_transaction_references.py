import inspect

from app.api.routes.purchases import receive_purchase
from app.api.routes.sales import post_sale


def test_posted_stock_and_accounting_use_the_server_assigned_folio():
    # Folios are generated inside these commands. Referencing data.number
    # afterwards would create null/forged kardex or accounting references for
    # modern clients that do not send a number.
    assert "data.number" not in inspect.getsource(post_sale)
    assert "data.number" not in inspect.getsource(receive_purchase)
