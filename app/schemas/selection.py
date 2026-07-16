"""
app/schemas/selection.py
─────────────────────────
Pydantic models for Selection creation and retrieval.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.node import NodeResponse


class SelectionCreate(BaseModel):
    """Request body for creating a new selection."""

    name: str = Field(..., min_length=1, max_length=256, description="Selection name, e.g. 'Safety'")
    description: str = Field("", description="Optional description of what this selection covers")
    version_id: int = Field(..., description="ID of the DocumentVersion to pin this selection to")
    node_ids: list[int] = Field(
        ..., min_length=1, description="List of DocumentNode IDs to include in this selection"
    )


class SelectionResponse(BaseModel):
    """Full selection resource returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    document_version_id: int
    created_at: datetime
    node_count: int = Field(0, description="Number of nodes in this selection")


class SelectionDetailResponse(SelectionResponse):
    """Selection with embedded node list."""

    nodes: list[NodeResponse] = Field(default_factory=list)


class SelectionListResponse(BaseModel):
    """List of selections."""

    total: int
    items: list[SelectionResponse]
