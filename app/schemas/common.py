from datetime import datetime
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


class MembershipUpdate(BaseModel):
    role_code: str | None = None
    is_active: bool | None = None


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
