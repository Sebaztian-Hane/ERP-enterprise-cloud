from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.routes.workflow import WorkflowLine
from app.schemas.inventory import InventoryLotReceive, InventorySerialReceive, InventoryTransferCreate, StockAdjustment
from app.schemas.transactions import PurchaseLineCreate, SaleLineCreate


@pytest.mark.parametrize(
    "factory, payload",
    [
        (SaleLineCreate, {"product_id": uuid4(), "quantity": "2.5", "unit_price": "10"}),
        (PurchaseLineCreate, {"product_id": uuid4(), "quantity": "2.5", "unit_cost": "10"}),
        (StockAdjustment, {"product_id": uuid4(), "warehouse_id": uuid4(), "quantity": "2.5"}),
        (InventoryLotReceive, {"product_id": uuid4(), "warehouse_id": uuid4(), "lot_number": "L-001", "quantity": "2.5", "unit_cost": "10"}),
        (InventoryTransferCreate, {"number": "TR-001", "product_id": uuid4(), "source_warehouse_id": uuid4(), "destination_warehouse_id": uuid4(), "quantity": "2.5"}),
        (WorkflowLine, {"product_id": uuid4(), "quantity": "2.5", "unit_value": "10"}),
    ],
)
def test_quantity_models_reject_decimal_units(factory, payload):
    with pytest.raises(ValidationError):
        factory(**payload)


def test_stock_adjustment_accepts_negative_whole_units():
    adjustment = StockAdjustment(product_id=uuid4(), warehouse_id=uuid4(), quantity="-3", reason="Corrección de conteo físico")
    assert adjustment.quantity == -3


def test_serial_receipt_rejects_duplicate_serials():
    with pytest.raises(ValidationError):
        InventorySerialReceive(
            product_id=uuid4(), warehouse_id=uuid4(), unit_cost="10",
            serial_numbers=["SER-001", "SER-001"],
        )
