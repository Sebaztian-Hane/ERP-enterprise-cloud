from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, Field, field_validator, model_validator
from app.schemas.common import ORMModel


class SaleLineCreate(BaseModel):
    product_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=0)
    unit_price: Decimal = Field(ge=0, max_digits=14, decimal_places=2)

    @field_validator("quantity")
    @classmethod
    def quantity_must_be_whole(cls, value: Decimal) -> Decimal:
        if value != value.to_integral_value():
            raise ValueError("La cantidad debe ser un número entero")
        return value


class SaleCreate(BaseModel):
    number: str = Field(min_length=1, max_length=40)
    customer_id: UUID
    warehouse_id: UUID
    lines: list[SaleLineCreate] = Field(min_length=1)
    note: str | None = None

    @model_validator(mode="after")
    def unique_products(self):
        if len({line.product_id for line in self.lines}) != len(self.lines):
            raise ValueError("Un producto solo puede aparecer una vez")
        return self


class SaleOut(ORMModel):
    id: UUID
    number: str
    customer_id: UUID
    warehouse_id: UUID
    status: str
    total: Decimal
    note: str | None


class PurchaseLineCreate(BaseModel):
    product_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=0)
    unit_cost: Decimal = Field(ge=0, max_digits=14, decimal_places=2)

    @field_validator("quantity")
    @classmethod
    def quantity_must_be_whole(cls, value: Decimal) -> Decimal:
        if value != value.to_integral_value():
            raise ValueError("La cantidad debe ser un número entero")
        return value


class PurchaseCreate(BaseModel):
    number: str = Field(min_length=1, max_length=40)
    supplier_id: UUID
    warehouse_id: UUID
    lines: list[PurchaseLineCreate] = Field(min_length=1)
    note: str | None = None
    supplier_document_type: str | None = Field(default=None, max_length=8)
    supplier_document_series: str | None = Field(default=None, max_length=8)
    supplier_document_number: str | None = Field(default=None, max_length=16)
    supplier_document_date: date | None = None
    supplier_tax_amount: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)

    @model_validator(mode="after")
    def unique_products(self):
        if len({line.product_id for line in self.lines}) != len(self.lines):
            raise ValueError("Un producto solo puede aparecer una vez")
        return self


class PurchaseOut(ORMModel):
    id: UUID
    number: str
    supplier_id: UUID
    warehouse_id: UUID
    status: str
    total: Decimal
    note: str | None
    supplier_document_type: str | None
    supplier_document_series: str | None
    supplier_document_number: str | None
    supplier_document_date: datetime | None
    supplier_tax_amount: Decimal | None


class CancelTransaction(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
