"""
app/schemas/document.py
────────────────────────
Pydantic request/response models for Document and DocumentVersion resources.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class DocumentBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=512, description="Document title")
    file_name: str = Field(..., description="Original file name")


class DocumentResponse(DocumentBase):
    """Full document resource returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    version_count: int = Field(0, description="Number of ingested versions")


class DocumentListResponse(BaseModel):
    """Paginated list of documents."""

    total: int
    items: list[DocumentResponse]


# ── Version ───────────────────────────────────────────────────────────────────


class DocumentVersionResponse(BaseModel):
    """A single document version resource."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    version_number: int
    file_hash: str
    ingested_at: datetime
    node_count: int = Field(0, description="Number of nodes in this version")


class DocumentVersionListResponse(BaseModel):
    """All versions for a document."""

    document_id: int
    versions: list[DocumentVersionResponse]


# ── Ingestion ─────────────────────────────────────────────────────────────────


class IngestionResponse(BaseModel):
    """Summary returned after successful PDF ingestion."""

    document_id: int
    version_id: int
    version_number: int
    nodes_created: int
    file_hash: str
    message: str
