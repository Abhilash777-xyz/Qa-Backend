"""
app/api/v1/changes.py
──────────────────────
Change detection endpoints.

GET /api/v1/changes/{node_id}            – Has this node changed vs latest?
GET /api/v1/changes/versions?v1=&v2=     – Full diff between two versions.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.schemas.diff import NodeChangeResponse, VersionDiffResponse
from app.services.version_service import compare_versions, get_node_change

router = APIRouter(prefix="/changes", tags=["Change Detection"])


@router.get(
    "/versions",
    response_model=VersionDiffResponse,
    summary="Full diff between two document versions",
)
def version_diff(
    v1: int = Query(..., description="Baseline (older) version ID"),
    v2: int = Query(..., description="Comparison (newer) version ID"),
    db: Session = Depends(get_db),
) -> VersionDiffResponse:
    """
    Compare every node between two versions of the same document.

    Each node is classified as:
    - **UNCHANGED** – same node_uuid, same content hash
    - **MODIFIED**  – same node_uuid, different content hash
    - **ADDED**     – node exists only in v2
    - **REMOVED**   – node exists only in v1
    """
    return compare_versions(db=db, v1_id=v1, v2_id=v2)


@router.get(
    "/{node_id}",
    response_model=NodeChangeResponse,
    summary="Check if a node has changed vs the latest version",
)
def node_change(node_id: int, db: Session = Depends(get_db)) -> NodeChangeResponse:
    """
    Return change information for a specific node compared to the latest
    available version of the same document.

    Response includes:
    - ``changed``: boolean
    - ``summary``: human-readable description
    - ``old_hash`` / ``new_hash``: SHA-256 content fingerprints
    - ``status``: UNCHANGED | MODIFIED | REMOVED
    """
    return get_node_change(db=db, node_id=node_id)
