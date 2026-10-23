"""Validated commands for the fulfilment leg of a sales order."""

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class SalesDeliveryLineCreate(BaseModel):
    product_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=0)
    # Required only for serial-tracked products.  Lots are allocated FEFO by
    # the server so the browser never chooses an invalid or exhausted lot.
    serial_numbers: list[str] | None = Field(default=None, max_length=100)

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
        if any(not value for value in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("Las series de una línea deben ser únicas")
        return normalized


class SalesDeliveryCreate(BaseModel):
    number: str = Field(min_length=1, max_length=40)
    sales_order_id: UUID
    recipient_name: str | None = Field(default=None, max_length=160)
    recipient_document: str | None = Field(default=None, max_length=16)
    note: str | None = Field(default=None, max_length=2000)
    lines: list[SalesDeliveryLineCreate] = Field(min_length=1)

    @model_validator(mode="after")
    def each_product_appears_once(self):
        if len({line.product_id for line in self.lines}) != len(self.lines):
            raise ValueError("Un producto solo puede aparecer una vez en el despacho")
        return self


class SalesDeliveryDispatchLine(BaseModel):
    """Optional serial allocation for one already-created delivery line."""

    product_id: UUID
    serial_numbers: list[str] = Field(min_length=1, max_length=100)

    @field_validator("serial_numbers")
    @classmethod
    def serials_must_be_unique(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("Las series de una línea deben ser únicas")
        return normalized


class SalesDeliveryDispatch(BaseModel):
    lines: list[SalesDeliveryDispatchLine] = Field(default_factory=list)

    @model_validator(mode="after")
    def serial_products_appear_once(self):
        if len({line.product_id for line in self.lines}) != len(self.lines):
            raise ValueError("Un producto solo puede tener una asignación de series")
        return self
