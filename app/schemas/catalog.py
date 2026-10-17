from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, Field
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


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None


class CategoryOut(CategoryCreate, ORMModel):
    id: UUID


class ProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=160)
    category_id: UUID | None = None
    unit: str = Field(default="unit", min_length=1, max_length=16)
    sale_price: Decimal = Field(default=Decimal("0"), ge=0)


class ProductOut(ProductCreate, ORMModel):
    id: UUID
    is_active: bool


class SupplierCreate(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    email: str | None = None
    phone: str | None = None
    tax_id: str | None = None


class SupplierOut(SupplierCreate, ORMModel):
    id: UUID
    is_active: bool
