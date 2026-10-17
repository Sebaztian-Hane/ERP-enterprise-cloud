from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel
from app.schemas.common import ORMModel


class AccountReceivableOut(ORMModel):
    id: UUID
    sale_id: UUID
    customer_id: UUID
    original_amount: Decimal
    balance_amount: Decimal
    status: str


class AccountPayableOut(ORMModel):
    id: UUID
    purchase_id: UUID
    supplier_id: UUID
    original_amount: Decimal
    balance_amount: Decimal
    status: str
