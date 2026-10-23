from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class PettyCashFundCreate(BaseModel):
    code: str = Field(min_length=2, max_length=40)
    name: str = Field(min_length=3, max_length=120)
    cash_account_id: UUID
    funding_cash_account_id: UUID
    custodian_user_id: UUID
    fixed_amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)


class PettyCashExpenseCreate(BaseModel):
    number: str = Field(min_length=2, max_length=40)
    expense_date: date = Field(default_factory=date.today)
    description: str = Field(min_length=3, max_length=500)
    merchant_name: str | None = Field(default=None, max_length=160)
    supplier_tax_id: str | None = Field(default=None, max_length=16)
    receipt_number: str | None = Field(default=None, max_length=100)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    expense_account_code: str = Field(default="6590", min_length=3, max_length=24)
    cost_center_id: UUID | None = None
    document_id: UUID | None = None


class PettyCashReplenishmentCreate(BaseModel):
    number: str = Field(min_length=2, max_length=40)
