from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Message(BaseModel):
    detail: str


class TenantOut(ORMModel):
    id: UUID
    name: str
    timezone: str
    is_active: bool


class PlatformTenantOut(TenantOut):
    created_at: datetime
    active_members: int


class PlatformTenantUpdate(BaseModel):
    is_active: bool


class PlatformTenantCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    timezone: str = Field(default="America/Mexico_City", min_length=3, max_length=64)
    owner_email: EmailStr
    temporary_password: str = Field(min_length=12, max_length=128)
    issuer_ruc: str = Field(pattern=r"^\d{11}$")
    fiscal_address: str = Field(min_length=4, max_length=600)
    apisunat_persona_id: str = Field(min_length=4, max_length=160)
    apisunat_persona_token: str = Field(min_length=12, max_length=1000)
    apisunat_invoice_series: str = Field(default="F001", pattern=r"^[A-Z0-9]{4,8}$")
    apisunat_receipt_series: str = Field(default="B001", pattern=r"^[A-Z0-9]{4,8}$")


class APISunatCredentialUpsert(BaseModel):
    legal_entity_id: UUID
    persona_id: str = Field(min_length=4, max_length=160)
    persona_token: str = Field(min_length=12, max_length=1000)
    invoice_series: str = Field(default="F001", pattern=r"^[A-Z0-9]{4,8}$")
    receipt_series: str = Field(default="B001", pattern=r"^[A-Z0-9]{4,8}$")


class APISunatCredentialStatus(ORMModel):
    legal_entity_id: UUID
    issuer_ruc: str
    invoice_series: str
    receipt_series: str
    is_configured: bool
    source: str


class ModuleToggle(BaseModel):
    module_code: str = Field(min_length=2, max_length=50)
    is_enabled: bool


class TenantCreate(BaseModel):
    name: str
    timezone: str = "America/Mexico_City"


class MembershipCreate(BaseModel):
    user_id: UUID
    role_code: str


class MembershipOut(ORMModel):
    id: UUID
    tenant_id: UUID
    user_id: UUID
    role_code: str
    is_active: bool


class MembershipUpdate(BaseModel):
    role_code: str | None = None
    is_active: bool | None = None


class MembershipInvite(BaseModel):
    email: EmailStr
    role_code: str
    temporary_password: str = Field(min_length=12, max_length=128)


class PasswordChangeRequest(BaseModel):
    new_password: str = Field(min_length=12, max_length=128)


class PasswordChangeConfirm(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")
    new_password: str = Field(min_length=12, max_length=128)


class AuditLogOut(ORMModel):
    id: UUID
    actor_user_id: UUID
    action: str
    module: str
    entity: str
    entity_id: UUID | None
    outcome: str
    detail: str | None
    created_at: datetime
