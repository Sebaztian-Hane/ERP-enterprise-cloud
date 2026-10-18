from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, Field, field_validator, model_validator
from app.schemas.common import ORMModel


class WarehouseCreate(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    address: str | None = None


class WarehouseOut(WarehouseCreate, ORMModel):
    id: UUID
    is_active: bool


class WarehouseUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=40)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    address: str | None = None
    is_active: bool | None = None


class StockAdjustment(BaseModel):
    product_id: UUID
    warehouse_id: UUID
    quantity: Decimal = Field(max_digits=14, decimal_places=3)
    reference: str | None = Field(default=None, max_length=100)
    note: str | None = None

    @field_validator("quantity")
    @classmethod
    def quantity_must_not_be_zero(cls, value: Decimal) -> Decimal:
        if value == 0:
            raise ValueError("La cantidad no puede ser cero")
        return value


class InventoryOut(ORMModel):
    id: UUID
    product_id: UUID
    warehouse_id: UUID
    quantity: Decimal


class InventoryMovementOut(ORMModel):
    id: UUID
    product_id: UUID
    warehouse_id: UUID
    movement_type: str
    quantity: Decimal
    reference: str | None
    note: str | None


class InventoryTransferCreate(BaseModel):
    number: str = Field(min_length=1, max_length=40)
    product_id: UUID
    source_warehouse_id: UUID
    destination_warehouse_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    note: str | None = None

    @model_validator(mode="after")
    def different_warehouses(self):
        if self.source_warehouse_id == self.destination_warehouse_id:
            raise ValueError("Los almacenes deben ser diferentes")
        return self


class InventoryTransferOut(ORMModel):
    id: UUID
    number: str
    product_id: UUID
    source_warehouse_id: UUID
    destination_warehouse_id: UUID
    quantity: Decimal
    status: str
    note: str | None
