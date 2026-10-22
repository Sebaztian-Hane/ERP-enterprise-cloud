from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel


class LegalEntityCreate(BaseModel):
    code: str = Field(min_length=2, max_length=24)
    legal_name: str = Field(min_length=2, max_length=200)
    tax_id: str = Field(pattern=r"^\d{11}$")
    fiscal_address: str = Field(min_length=5, max_length=1000)
    trade_name: str | None = Field(default=None, max_length=200)
    tax_regime: str | None = Field(default=None, max_length=80)
    ubigeo: str | None = Field(default=None, pattern=r"^\d{6}$")
    is_default: bool = False

    @field_validator("code", "legal_name", "fiscal_address", "trade_name", "tax_regime", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class LegalEntityOut(LegalEntityCreate, ORMModel):
    id: UUID
    tenant_id: UUID
    is_active: bool


class LegalEntityUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=2, max_length=24)
    legal_name: str | None = Field(default=None, min_length=2, max_length=200)
    tax_id: str | None = Field(default=None, pattern=r"^\d{11}$")
    fiscal_address: str | None = Field(default=None, min_length=5, max_length=1000)
    trade_name: str | None = Field(default=None, max_length=200)
    tax_regime: str | None = Field(default=None, max_length=80)
    ubigeo: str | None = Field(default=None, pattern=r"^\d{6}$")
    is_default: bool | None = None
    is_active: bool | None = None

    @field_validator("code", "legal_name", "fiscal_address", "trade_name", "tax_regime", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class BranchCreate(BaseModel):
    legal_entity_id: UUID
    code: str = Field(min_length=2, max_length=24)
    name: str = Field(min_length=2, max_length=160)
    address: str | None = Field(default=None, max_length=1000)
    ubigeo: str | None = Field(default=None, pattern=r"^\d{6}$")

    @field_validator("code", "name", "address", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class BranchOut(BranchCreate, ORMModel):
    id: UUID
    tenant_id: UUID
    is_active: bool


class BranchUpdate(BaseModel):
    legal_entity_id: UUID | None = None
    code: str | None = Field(default=None, min_length=2, max_length=24)
    name: str | None = Field(default=None, min_length=2, max_length=160)
    address: str | None = Field(default=None, max_length=1000)
    ubigeo: str | None = Field(default=None, pattern=r"^\d{6}$")
    is_active: bool | None = None

    @field_validator("code", "name", "address", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value
