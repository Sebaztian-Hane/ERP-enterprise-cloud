import uuid

from sqlalchemy import ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import TimestampedTenantModel


class EnterpriseRecord(TimestampedTenantModel):
    __tablename__ = "enterprise_records"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "resource_code", "number"),
        Index("ix_enterprise_records_scope", "tenant_id", "resource_code", "status"),
    )
    module_code: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_code: Mapped[str] = mapped_column(String(40), nullable=False)
    number: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    derived: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    updated_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


class EnterpriseRecordEvent(TimestampedTenantModel):
    __tablename__ = "enterprise_record_events"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "record_id"], ["enterprise_records.tenant_id", "enterprise_records.id"]),
        UniqueConstraint("tenant_id", "record_id", "version"),
    )
    record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(24))
    to_state: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
