"""
app/schemas/diff.py
────────────────────
Pydantic models for version comparison and change detection APIs.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ChangeStatus(str, Enum):
    UNCHANGED = "UNCHANGED"
    MODIFIED = "MODIFIED"
    ADDED = "ADDED"
    REMOVED = "REMOVED"


class NodeDiff(BaseModel):
    """
    Change record for a single node between two document versions.
    """

    node_uuid: str
    status: ChangeStatus
    numbering: str
    title: str

    # Version 1 fields (None if ADDED).
    v1_node_id: Optional[int] = None
    v1_hash: Optional[str] = None
    v1_body_text: Optional[str] = None

    # Version 2 fields (None if REMOVED).
    v2_node_id: Optional[int] = None
    v2_hash: Optional[str] = None
    v2_body_text: Optional[str] = None


class VersionDiffResponse(BaseModel):
    """Full diff between two document versions."""

    document_id: int
    v1_version_id: int
    v1_version_number: int
    v2_version_id: int
    v2_version_number: int

    total_nodes: int
    unchanged: int
    modified: int
    added: int
    removed: int

    diffs: list[NodeDiff]


class NodeChangeResponse(BaseModel):
    """
    Response for GET /api/v1/changes/{node_id}.

    Answers: has this specific node changed between its version and the latest?
    """

    node_id: int
    node_uuid: str
    changed: bool
    summary: str

    old_hash: Optional[str] = None
    new_hash: Optional[str] = None
    old_version_id: Optional[int] = None
    new_version_id: Optional[int] = None
    old_version_number: Optional[int] = None
    new_version_number: Optional[int] = None
    status: ChangeStatus = ChangeStatus.UNCHANGED
