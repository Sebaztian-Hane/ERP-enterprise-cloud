from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, Field, field_validator, model_validator
from app.schemas.common import ORMModel


class SaleLineCreate(BaseModel):
    product_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=0)
    unit_price: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    serial_numbers: list[str] | None = Field(default=None, max_length=100)

    @field_validator("quantity")
    @classmethod
    def quantity_must_be_whole(cls, value: Decimal) -> Decimal:
        if value != value.to_integral_value():
            raise ValueError("La cantidad debe ser un número entero")
        return value

    @field_validator("serial_numbers")
    @classmethod
    def sale_serials_must_be_unique(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return values
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("Las series de una línea deben ser únicas")
        return normalized


class SaleCustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    tax_id: str = Field(min_length=8, max_length=11)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=40)

    @field_validator("tax_id")
    @classmethod
    def fiscal_id_must_be_dni_or_ruc(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) not in {8, 11} or not normalized.isdigit():
            raise ValueError("El documento debe ser un DNI de 8 dígitos o RUC de 11 dígitos")
        return normalized


class SaleCreate(BaseModel):
    # Retained as optional input only for older clients. The ERP now assigns
    # sales and purchases a server-side, scoped operational folio.
    number: str | None = Field(default=None, min_length=1, max_length=40)
    customer_id: UUID | None = None
    customer: SaleCustomerCreate | None = None
    warehouse_id: UUID
    lines: list[SaleLineCreate] = Field(min_length=1)
    note: str | None = None
    due_date: date | None = None

    @model_validator(mode="after")
    def unique_products(self):
        if len({line.product_id for line in self.lines}) != len(self.lines):
            raise ValueError("Un producto solo puede aparecer una vez")
        if (self.customer_id is None) == (self.customer is None):
            raise ValueError("Selecciona un cliente existente o registra un cliente con DNI/RUC")
        return self


class SaleOut(ORMModel):
    id: UUID
    number: str
    customer_id: UUID
    warehouse_id: UUID
    legal_entity_id: UUID | None
    branch_id: UUID | None
    status: str
    total: Decimal
    note: str | None


class PurchaseLineCreate(BaseModel):
    product_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=0)
    unit_cost: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    lot_number: str | None = Field(default=None, max_length=100)
    expiration_date: date | None = None
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
        if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("Las series de una línea deben ser únicas")
        return normalized


class PurchaseCreate(BaseModel):
    number: str | None = Field(default=None, min_length=1, max_length=40)
    supplier_id: UUID
    warehouse_id: UUID
    lines: list[PurchaseLineCreate] = Field(min_length=1)
    note: str | None = None
    supplier_document_type: str | None = Field(default=None, max_length=8)
    supplier_document_series: str | None = Field(default=None, max_length=8)
    supplier_document_number: str | None = Field(default=None, max_length=16)
    supplier_document_date: date | None = None
    supplier_tax_amount: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    due_date: date | None = None

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
    legal_entity_id: UUID | None
    branch_id: UUID | None
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
