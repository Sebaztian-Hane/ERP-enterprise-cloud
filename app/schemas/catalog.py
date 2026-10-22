from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field, field_validator
from app.schemas.common import ORMModel


class CustomerCreate(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    email: str | None = None
    phone: str | None = None
    tax_id: str | None = None
    credit_limit: Decimal = Field(default=Decimal("0"), ge=0)


class CustomerOut(CustomerCreate, ORMModel):
    id: UUID
    is_active: bool


class CustomerUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=40)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    email: str | None = None
    phone: str | None = None
    tax_id: str | None = None
    credit_limit: Decimal | None = Field(default=None, ge=0)
    is_active: bool | None = None


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None


class CategoryOut(CategoryCreate, ORMModel):
    id: UUID


class ProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=160)
    category_id: UUID | None = None
    unit_id: UUID
    sale_price: Decimal = Field(default=Decimal("0"), ge=0)
    tracking_mode: Literal["none", "lot", "serial"] = "none"
    cost_method: Literal["weighted_average", "fifo"] = "weighted_average"
    average_cost: Decimal = Field(default=Decimal("0"), ge=0)


class ProductOut(ProductCreate, ORMModel):
    id: UUID
    unit: str
    unit_code: str
    unit_name: str
    is_active: bool


class ProductUpdate(BaseModel):
    sku: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    category_id: UUID | None = None
    unit_id: UUID | None = None
    sale_price: Decimal | None = Field(default=None, ge=0)
    tracking_mode: Literal["none", "lot", "serial"] | None = None
    cost_method: Literal["weighted_average", "fifo"] | None = None
    average_cost: Decimal | None = Field(default=None, ge=0)
    is_active: bool | None = None


class SupplierCreate(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    email: str | None = None
    phone: str | None = None
    tax_id: str | None = None
    address: str | None = Field(default=None, max_length=500)

    @field_validator("tax_id")
    @classmethod
    def validate_ruc(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip()
        if not normalized.isdigit() or len(normalized) != 11:
            raise ValueError("El RUC debe tener 11 dígitos")
        return normalized


class SupplierOut(SupplierCreate, ORMModel):
    id: UUID
    is_active: bool


class SupplierUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=40)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    email: str | None = None
    phone: str | None = None
    tax_id: str | None = None
    address: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None

    @field_validator("tax_id")
    @classmethod
    def validate_ruc(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip()
        if not normalized.isdigit() or len(normalized) != 11:
            raise ValueError("El RUC debe tener 11 dígitos")
        return normalized
