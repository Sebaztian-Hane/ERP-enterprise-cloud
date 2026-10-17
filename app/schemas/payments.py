from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, Field, model_validator
from app.schemas.common import ORMModel


class PaymentAllocationCreate(BaseModel):
    receivable_id: UUID | None = None
    payable_id: UUID | None = None
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)

    @model_validator(mode="after")
    def exactly_one_account(self):
        if bool(self.receivable_id) == bool(self.payable_id):
            raise ValueError("La aplicación debe indicar una sola cuenta")
        return self


class PaymentCreate(BaseModel):
    number: str = Field(min_length=1, max_length=40)
    payment_type: str = Field(pattern="^(receipt|payment)$")
    customer_id: UUID | None = None
    supplier_id: UUID | None = None
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    method: str = Field(min_length=1, max_length=32)
    reference: str | None = Field(default=None, max_length=100)
    note: str | None = None
    cash_account_id: UUID
    allocations: list[PaymentAllocationCreate] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_counterparty(self):
        if self.payment_type == "receipt" and (not self.customer_id or self.supplier_id):
            raise ValueError("Un cobro requiere solo un cliente")
        if self.payment_type == "payment" and (not self.supplier_id or self.customer_id):
            raise ValueError("Un pago requiere solo un proveedor")
        if sum((line.amount for line in self.allocations), Decimal("0")) != self.amount:
            raise ValueError("La suma de aplicaciones debe coincidir con el importe")
        return self


class PaymentOut(ORMModel):
    id: UUID
    number: str
    payment_type: str
    cash_account_id: UUID
    customer_id: UUID | None
    supplier_id: UUID | None
    amount: Decimal
    method: str
    reference: str | None
    note: str | None
