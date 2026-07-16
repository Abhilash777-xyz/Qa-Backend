"""
app/api/v1/selections.py
─────────────────────────
Selection management endpoints.

POST /api/v1/selections            – Create a new named selection.
GET  /api/v1/selections            – List all selections.
GET  /api/v1/selections/{id}       – Get a selection with its nodes.
GET  /api/v1/selections/{id}/nodes – Get only the nodes for a selection.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.schemas.node import NodeResponse
from app.schemas.selection import (
    SelectionCreate,
    SelectionDetailResponse,
    SelectionListResponse,
)
from app.services.selection_service import (
    create_selection,
    get_selection,
    get_selection_nodes,
    list_selections,
)

router = APIRouter(prefix="/selections", tags=["Selections"])


@router.post(
    "",
    response_model=SelectionDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a version-pinned named selection",
)
def create(
    payload: SelectionCreate,
    db: Session = Depends(get_db),
) -> SelectionDetailResponse:
    """
    Create a named selection of document nodes pinned to a specific version.

    Once created, the selection's ``document_version_id`` is immutable —
    importing a newer document version will not change what this selection
    resolves to.

    Example body::

        {
          "name": "Safety",
          "description": "Safety-critical sections for test generation",
          "version_id": 1,
          "node_ids": [10, 11, 12]
        }
    """
    return create_selection(db=db, payload=payload)


@router.get(
    "",
    response_model=SelectionListResponse,
    summary="List all selections",
)
def list_all(db: Session = Depends(get_db)) -> SelectionListResponse:
    """Return all named selections ordered by creation date (newest first)."""
    return list_selections(db=db)


@router.get(
    "/{selection_id}",
    response_model=SelectionDetailResponse,
    summary="Get a selection with embedded nodes",
)
def get(selection_id: int, db: Session = Depends(get_db)) -> SelectionDetailResponse:
    """
    Return a selection and all its nodes, resolved from the pinned version.
    """
    return get_selection(db=db, selection_id=selection_id)


@router.get(
    "/{selection_id}/nodes",
    response_model=list[NodeResponse],
    summary="Get only the nodes for a selection",
)
def get_nodes(
    selection_id: int, db: Session = Depends(get_db)
) -> list[NodeResponse]:
    """Return just the node list for a selection (without selection metadata)."""
    nodes = get_selection_nodes(db=db, selection_id=selection_id)
    return [NodeResponse.model_validate(n, from_attributes=True) for n in nodes]
