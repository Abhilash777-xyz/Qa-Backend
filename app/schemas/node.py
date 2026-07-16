"""
app/schemas/node.py
────────────────────
Pydantic response models for DocumentNode resources.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class NodeBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_version_id: int
    node_uuid: str
    parent_id: Optional[int] = None
    heading_level: int
    numbering: str
    title: str
    node_type: str
    body_text: str
    content_hash: str
    created_at: datetime


class NodeResponse(NodeBase):
    """Flat node resource — no children embedded."""
    pass


class NodeTreeResponse(NodeBase):
    """
    Recursive node resource with children embedded.

    Used by the /tree endpoint to return a full subtree in one call.
    """

    children: list["NodeTreeResponse"] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


NodeTreeResponse.model_rebuild()


class ChildrenResponse(BaseModel):
    """Direct children of a node."""

    parent_id: int
    children: list[NodeResponse]
