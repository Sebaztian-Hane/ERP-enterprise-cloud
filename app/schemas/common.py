from uuid import UUID
from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Message(BaseModel):
    detail: str


class TenantOut(ORMModel):
    id: UUID
    name: str
    timezone: str
    is_active: bool


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
