"""
app/api/v1/nodes.py
────────────────────
Routes for document node retrieval and navigation.

GET /api/v1/nodes/{id}           – Get a single node (flat).
GET /api/v1/nodes/{id}/children  – Get direct children of a node.
GET /api/v1/nodes/{id}/tree      – Get a full recursive subtree.
GET /api/v1/nodes                – List nodes for a version (paginated).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.database.session import get_db
from app.models.document import DocumentNode
from app.schemas.node import ChildrenResponse, NodeResponse, NodeTreeResponse

router = APIRouter(prefix="/nodes", tags=["Nodes"])


@router.get(
    "",
    response_model=list[NodeResponse],
    summary="List nodes for a document version",
)
def list_nodes(
    version_id: int = Query(..., description="DocumentVersion ID"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[NodeResponse]:
    """
    Return a flat paginated list of all nodes for a given document version.
    Use the /tree endpoint for the full hierarchy in one call.
    """
    nodes = (
        db.query(DocumentNode)
        .filter(DocumentNode.document_version_id == version_id)
        .order_by(DocumentNode.id)
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [NodeResponse.model_validate(n, from_attributes=True) for n in nodes]


@router.get(
    "/{node_id}",
    response_model=NodeResponse,
    summary="Get a single node by ID",
)
def get_node(node_id: int, db: Session = Depends(get_db)) -> NodeResponse:
    """Return a single node by its primary key."""
    node = db.get(DocumentNode, node_id)
    if node is None:
        raise NotFoundError(f"Node {node_id} not found.")
    return NodeResponse.model_validate(node, from_attributes=True)


@router.get(
    "/{node_id}/children",
    response_model=ChildrenResponse,
    summary="Get direct children of a node",
)
def get_children(node_id: int, db: Session = Depends(get_db)) -> ChildrenResponse:
    """Return all direct (non-recursive) children of the given node."""
    node = db.get(DocumentNode, node_id)
    if node is None:
        raise NotFoundError(f"Node {node_id} not found.")

    children = (
        db.query(DocumentNode)
        .filter(DocumentNode.parent_id == node_id)
        .order_by(DocumentNode.id)
        .all()
    )
    return ChildrenResponse(
        parent_id=node_id,
        children=[NodeResponse.model_validate(c, from_attributes=True) for c in children],
    )


@router.get(
    "/{node_id}/tree",
    response_model=NodeTreeResponse,
    summary="Get a full recursive subtree rooted at a node",
)
def get_tree(node_id: int, db: Session = Depends(get_db)) -> NodeTreeResponse:
    """
    Return the requested node with all descendants embedded recursively.

    This loads the full subtree in memory.  For very large documents, prefer
    the paginated /nodes endpoint and navigate level-by-level via /children.
    """
    node = db.get(DocumentNode, node_id)
    if node is None:
        raise NotFoundError(f"Node {node_id} not found.")

    # Pre-load all nodes for this version to avoid N+1 queries.
    all_nodes = (
        db.query(DocumentNode)
        .filter(DocumentNode.document_version_id == node.document_version_id)
        .all()
    )
    return _build_tree_response(node, all_nodes)


# ── Private helpers ────────────────────────────────────────────────────────────


def _build_tree_response(
    root: DocumentNode, all_nodes: list[DocumentNode]
) -> NodeTreeResponse:
    """
    Build a recursive NodeTreeResponse from a flat list of ORM nodes.
    Uses an id-indexed dict to avoid repeated list scans.
    """
    id_map: dict[int, DocumentNode] = {n.id: n for n in all_nodes}
    children_map: dict[int, list[DocumentNode]] = {}
    for n in all_nodes:
        if n.parent_id is not None:
            children_map.setdefault(n.parent_id, []).append(n)

    def _recurse(node: DocumentNode) -> NodeTreeResponse:
        resp = NodeTreeResponse.model_validate(node, from_attributes=True)
        resp.children = [
            _recurse(child)
            for child in sorted(children_map.get(node.id, []), key=lambda x: x.id)
        ]
        return resp

    return _recurse(root)
