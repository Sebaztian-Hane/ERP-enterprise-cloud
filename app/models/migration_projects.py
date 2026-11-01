"""Private staging area. Business rows are written only by the final command."""

import uuid
from sqlalchemy import ForeignKeyConstraint, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.models.entities import TimestampedTenantModel


class MigrationProject(TimestampedTenantModel):
    __tablename__ = "migration_projects"
    __table_args__ = (UniqueConstraint("tenant_id", "id"),)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    group_codes: Mapped[list] = mapped_column(JSONB, nullable=False)
    object_codes: Mapped[list] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    simulation: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    receipt: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class MigrationObject(TimestampedTenantModel):
    __tablename__ = "migration_project_objects"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "project_id"], ["migration_projects.tenant_id", "migration_projects.id"]),
        UniqueConstraint("tenant_id", "project_id", "object_code"),
    )
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    object_code: Mapped[str] = mapped_column(String(40), nullable=False)
    filename: Mapped[str] = mapped_column(String(200), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    rows: Mapped[list] = mapped_column(JSONB, nullable=False)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
