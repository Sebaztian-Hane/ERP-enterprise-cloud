from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class ElectronicDocumentCreate(BaseModel):
    document_type: Literal["01", "03"]


class VoidElectronicDocument(BaseModel):
    reason: str = Field(min_length=3, max_length=100)


class ElectronicDocumentOut(ORMModel):
    id: UUID
    sale_id: UUID
    legal_entity_id: UUID | None
    branch_id: UUID | None
    document_type: str
    series: str
    correlative: str
    file_name: str
    status: str
    provider_document_id: str | None
    taxable_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    currency: str
    xml_url: str | None
    cdr_url: str | None
    provider_response_hash: str | None
    provider_response_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class ElectronicDocumentArtifactOut(ORMModel):
    """Safe artifact metadata; storage paths and provider URLs remain private."""

    id: UUID
    artifact_type: str
    capture_status: str
    content_type: str | None
    size_bytes: int | None
    content_hash: str | None
    provider_document_id: str | None
    error_message: str | None
    captured_at: datetime


class ElectronicDocumentEventOut(ORMModel):
    id: UUID
    event_type: str
    document_status: str | None
    provider_document_id: str | None
    payload_hash: str | None
    detail: str | None
    created_at: datetime


class ElectronicDocumentTraceOut(BaseModel):
    document_id: UUID
    artifacts: list[ElectronicDocumentArtifactOut]
    events: list[ElectronicDocumentEventOut]


class ElectronicArtifactDownloadOut(BaseModel):
    url: str
    expires_in: int
