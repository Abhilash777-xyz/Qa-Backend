"""
app/services/selection_service.py
───────────────────────────────────
CRUD and resolution logic for named, version-pinned node selections.

Version-pinning invariant:
  A Selection's ``document_version_id`` is set at creation and never updated.
  All node lookups for a selection filter by that version ID, so importing
  Version 2 does not silently change what a selection resolves to.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging_config import get_logger
from app.models.document import DocumentNode, DocumentVersion
from app.models.selection import Selection
from app.schemas.selection import (
    SelectionCreate,
    SelectionDetailResponse,
    SelectionListResponse,
    SelectionResponse,
)

logger = get_logger(__name__)


def create_selection(db: Session, payload: SelectionCreate) -> SelectionDetailResponse:
    """
    Create a new named selection pinned to ``payload.version_id``.

    Args:
        db:      Active session.
        payload: SelectionCreate with name, version_id, and list of node IDs.

    Returns:
        SelectionDetailResponse including embedded node list.

    Raises:
        NotFoundError:   If the version or any node ID does not exist.
        ValidationError: If a node belongs to a different version.
    """
    # Validate version exists.
    version = db.get(DocumentVersion, payload.version_id)
    if version is None:
        raise NotFoundError(f"DocumentVersion {payload.version_id} not found.")

    # Validate and resolve nodes.
    nodes: list[DocumentNode] = []
    for node_id in payload.node_ids:
        node = db.get(DocumentNode, node_id)
        if node is None:
            raise NotFoundError(f"DocumentNode {node_id} not found.")
        if node.document_version_id != payload.version_id:
            raise ValidationError(
                f"Node {node_id} belongs to version {node.document_version_id}, "
                f"not the requested version {payload.version_id}."
            )
        nodes.append(node)

    # Deduplicate while preserving order.
    seen: set[int] = set()
    unique_nodes: list[DocumentNode] = []
    for n in nodes:
        if n.id not in seen:
            seen.add(n.id)
            unique_nodes.append(n)

    selection = Selection(
        name=payload.name,
        description=payload.description,
        document_version_id=payload.version_id,
        nodes=unique_nodes,
    )
    db.add(selection)
    db.flush()

    logger.info(
        "Created Selection id=%d name=%r version=%d nodes=%d",
        selection.id,
        selection.name,
        selection.document_version_id,
        len(unique_nodes),
    )

    return _to_detail_response(selection)


def list_selections(db: Session) -> SelectionListResponse:
    """Return all selections (no pagination for this assignment scope)."""
    selections = db.query(Selection).order_by(Selection.created_at.desc()).all()
    items = [_to_response(s) for s in selections]
    return SelectionListResponse(total=len(items), items=items)


def get_selection(db: Session, selection_id: int) -> SelectionDetailResponse:
    """
    Retrieve a selection by ID with its pinned nodes.

    Raises:
        NotFoundError if the selection does not exist.
    """
    selection = db.get(Selection, selection_id)
    if selection is None:
        raise NotFoundError(f"Selection {selection_id} not found.")
    return _to_detail_response(selection)


def get_selection_nodes(db: Session, selection_id: int) -> list[DocumentNode]:
    """
    Return the list of DocumentNode ORM objects for a selection.
    Used by the LLM service to reconstruct the text.

    Raises:
        NotFoundError if the selection does not exist.
    """
    selection = db.get(Selection, selection_id)
    if selection is None:
        raise NotFoundError(f"Selection {selection_id} not found.")
    return list(selection.nodes)


# ── Private helpers ────────────────────────────────────────────────────────────


def _to_response(s: Selection) -> SelectionResponse:
    return SelectionResponse(
        id=s.id,
        name=s.name,
        description=s.description,
        document_version_id=s.document_version_id,
        created_at=s.created_at,
        node_count=len(s.nodes),
    )


def _to_detail_response(s: Selection) -> SelectionDetailResponse:
    from app.schemas.node import NodeResponse

    return SelectionDetailResponse(
        id=s.id,
        name=s.name,
        description=s.description,
        document_version_id=s.document_version_id,
        created_at=s.created_at,
        node_count=len(s.nodes),
        nodes=[
            NodeResponse.model_validate(n, from_attributes=True) for n in s.nodes
        ],
    )
