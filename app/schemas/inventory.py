from datetime import date
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, Field, field_validator, model_validator
from app.schemas.common import ORMModel


class WarehouseCreate(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    address: str | None = None
    # A warehouse can remain unassigned while a tenant is being configured,
    # but an assigned branch is always checked in the route against its tenant.
    branch_id: UUID | None = None


class WarehouseOut(WarehouseCreate, ORMModel):
    id: UUID
    is_active: bool


class WarehouseUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=40)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    address: str | None = None
    branch_id: UUID | None = None
    is_active: bool | None = None


class StockAdjustment(BaseModel):
    product_id: UUID
    warehouse_id: UUID
    quantity: Decimal = Field(max_digits=14, decimal_places=0)
    reference: str | None = Field(default=None, max_length=100)
    reason: str = Field(min_length=3, max_length=240)
    note: str | None = None

    @field_validator("quantity")
    @classmethod
    def quantity_must_not_be_zero(cls, value: Decimal) -> Decimal:
        if value == 0:
            raise ValueError("La cantidad no puede ser cero")
        if value != value.to_integral_value():
            raise ValueError("La cantidad debe ser un número entero")
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


class InventoryLotReceive(BaseModel):
    product_id: UUID
    warehouse_id: UUID
    lot_number: str = Field(min_length=1, max_length=100)
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=0)
    unit_cost: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    expiration_date: date | None = None
    note: str | None = None

    @field_validator("quantity")
    @classmethod
    def lot_quantity_must_be_whole(cls, value: Decimal) -> Decimal:
        if value != value.to_integral_value():
            raise ValueError("La cantidad de un lote debe ser un número entero")
        return value


class InventorySerialReceive(BaseModel):
    product_id: UUID
    warehouse_id: UUID
    serial_numbers: list[str] = Field(min_length=1, max_length=100)
    unit_cost: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    note: str | None = None

    @field_validator("serial_numbers")
    @classmethod
    def serials_must_be_unique(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("Cada serie debe tener un valor único")
        if any(len(value) > 120 for value in normalized):
            raise ValueError("Una serie no puede superar 120 caracteres")
        return normalized


class InventoryTransferCreate(BaseModel):
    number: str = Field(min_length=1, max_length=40)
    product_id: UUID
    source_warehouse_id: UUID
    destination_warehouse_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=0)
    serial_numbers: list[str] | None = Field(default=None, max_length=100)
    note: str | None = None

    @model_validator(mode="after")
    def different_warehouses(self):
        if self.source_warehouse_id == self.destination_warehouse_id:
            raise ValueError("Los almacenes deben ser diferentes")
        return self

    @field_validator("quantity")
    @classmethod
    def quantity_must_be_whole(cls, value: Decimal) -> Decimal:
        if value != value.to_integral_value():
            raise ValueError("La cantidad debe ser un número entero")
        return value

    @field_validator("serial_numbers")
    @classmethod
    def serials_must_be_unique(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return values
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("Las series de una transferencia deben ser únicas")
        return normalized

    @model_validator(mode="after")
    def serial_count_matches_quantity(self):
        if self.serial_numbers is not None and len(self.serial_numbers) != int(self.quantity):
            raise ValueError("La cantidad de series debe coincidir con la cantidad transferida")
        return self


class InventoryTransferOut(ORMModel):
    id: UUID
    number: str
    product_id: UUID
    source_warehouse_id: UUID
    destination_warehouse_id: UUID
    quantity: Decimal
    status: str
    serial_numbers: list[str] | None
    note: str | None
