"""Commands for typed operational documents, independent of existing postings."""

from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class RecordCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    number: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=2, max_length=160)
    data: dict[str, Any]


class RecordEdit(RecordCreate):
    version: int = Field(ge=1)


class RecordTransition(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    version: int = Field(ge=1)
    to_state: str = Field(min_length=2, max_length=24)
    note: str = Field(min_length=3, max_length=2000)
    completion: dict[str, Any] = Field(default_factory=dict)
