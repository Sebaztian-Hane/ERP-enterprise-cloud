from uuid import UUID
from pydantic import BaseModel
from app.schemas.common import ORMModel


class DocumentOut(ORMModel):
    id: UUID
    original_filename: str
    content_type: str
    size_bytes: int
    entity_type: str | None
    entity_id: UUID | None


class SignedUrlOut(BaseModel):
    url: str
    expires_in: int
