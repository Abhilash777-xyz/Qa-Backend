"""
app/services/version_service.py
─────────────────────────────────
Compares two document versions and classifies every node as
UNCHANGED / MODIFIED / ADDED / REMOVED.

Matching strategy (documented in APPROACH.md):
─────────────────────────────────────────────
Primary key:  ``node_uuid``
  Most nodes have a dotted numbering string (e.g. "3.2.1") which is used
  directly as ``node_uuid``.  These are stable across minor edits.

Fallback:     ``slugify(title)`` under the same parent
  For un-numbered headings, the slug of the title is used.  If a section is
  renamed, it will appear as REMOVED + ADDED (no fuzzy matching is applied
  to avoid false positives).

Classification rules:
  - UNCHANGED : same node_uuid, same content_hash
  - MODIFIED  : same node_uuid, different content_hash
  - ADDED     : node_uuid exists only in v2
  - REMOVED   : node_uuid exists only in v1
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.logging_config import get_logger
from app.models.document import DocumentNode, DocumentVersion
from app.schemas.diff import ChangeStatus, NodeDiff, NodeChangeResponse, VersionDiffResponse

logger = get_logger(__name__)


def compare_versions(
    db: Session,
    v1_id: int,
    v2_id: int,
) -> VersionDiffResponse:
    """
    Produce a full diff between two document versions.

    Args:
        db:    Active session.
        v1_id: ID of the baseline (older) DocumentVersion.
        v2_id: ID of the comparison (newer) DocumentVersion.

    Returns:
        VersionDiffResponse with per-node diff entries and summary counts.

    Raises:
        NotFoundError if either version does not exist.
    """
    v1 = _get_version_or_404(db, v1_id)
    v2 = _get_version_or_404(db, v2_id)

    v1_nodes = {n.node_uuid: n for n in _load_nodes(db, v1_id)}
    v2_nodes = {n.node_uuid: n for n in _load_nodes(db, v2_id)}

    all_uuids = set(v1_nodes) | set(v2_nodes)
    diffs: list[NodeDiff] = []

    for uuid in sorted(all_uuids):
        n1 = v1_nodes.get(uuid)
        n2 = v2_nodes.get(uuid)

        if n1 and n2:
            if n1.content_hash == n2.content_hash:
                status = ChangeStatus.UNCHANGED
            else:
                status = ChangeStatus.MODIFIED
        elif n2 and not n1:
            status = ChangeStatus.ADDED
        else:
            status = ChangeStatus.REMOVED

        diffs.append(
            NodeDiff(
                node_uuid=uuid,
                status=status,
                numbering=(n1 or n2).numbering,  # type: ignore[union-attr]
                title=(n1 or n2).title,  # type: ignore[union-attr]
                v1_node_id=n1.id if n1 else None,
                v1_hash=n1.content_hash if n1 else None,
                v1_body_text=n1.body_text if n1 else None,
                v2_node_id=n2.id if n2 else None,
                v2_hash=n2.content_hash if n2 else None,
                v2_body_text=n2.body_text if n2 else None,
            )
        )

    counts = {s: sum(1 for d in diffs if d.status == s) for s in ChangeStatus}

    logger.info(
        "Version diff v%d vs v%d: unchanged=%d modified=%d added=%d removed=%d",
        v1.version_number,
        v2.version_number,
        counts[ChangeStatus.UNCHANGED],
        counts[ChangeStatus.MODIFIED],
        counts[ChangeStatus.ADDED],
        counts[ChangeStatus.REMOVED],
    )

    return VersionDiffResponse(
        document_id=v1.document_id,
        v1_version_id=v1.id,
        v1_version_number=v1.version_number,
        v2_version_id=v2.id,
        v2_version_number=v2.version_number,
        total_nodes=len(all_uuids),
        unchanged=counts[ChangeStatus.UNCHANGED],
        modified=counts[ChangeStatus.MODIFIED],
        added=counts[ChangeStatus.ADDED],
        removed=counts[ChangeStatus.REMOVED],
        diffs=diffs,
    )


def get_node_change(db: Session, node_id: int) -> NodeChangeResponse:
    """
    Check whether a specific node has changed compared to the latest version
    of the same document.

    Args:
        db:      Active session.
        node_id: ID of the DocumentNode to inspect.

    Returns:
        NodeChangeResponse describing the change status.

    Raises:
        NotFoundError if the node does not exist.
    """
    node = db.get(DocumentNode, node_id)
    if node is None:
        raise NotFoundError(f"Node {node_id} not found.")

    version = db.get(DocumentVersion, node.document_version_id)
    if version is None:
        raise NotFoundError(f"Version {node.document_version_id} not found.")

    # Find the latest version for this document.
    latest_version = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == version.document_id)
        .order_by(DocumentVersion.version_number.desc())
        .first()
    )

    if latest_version is None or latest_version.id == version.id:
        # No newer version — node is current.
        return NodeChangeResponse(
            node_id=node_id,
            node_uuid=node.node_uuid,
            changed=False,
            summary="No newer version exists; node is current.",
            old_hash=node.content_hash,
            new_hash=node.content_hash,
            old_version_id=version.id,
            new_version_id=version.id,
            old_version_number=version.version_number,
            new_version_number=version.version_number,
            status=ChangeStatus.UNCHANGED,
        )

    # Look for the same node_uuid in the latest version.
    latest_node = (
        db.query(DocumentNode)
        .filter(
            DocumentNode.document_version_id == latest_version.id,
            DocumentNode.node_uuid == node.node_uuid,
        )
        .first()
    )

    if latest_node is None:
        return NodeChangeResponse(
            node_id=node_id,
            node_uuid=node.node_uuid,
            changed=True,
            summary=f"Node was REMOVED in version {latest_version.version_number}.",
            old_hash=node.content_hash,
            new_hash=None,
            old_version_id=version.id,
            new_version_id=latest_version.id,
            old_version_number=version.version_number,
            new_version_number=latest_version.version_number,
            status=ChangeStatus.REMOVED,
        )

    if latest_node.content_hash == node.content_hash:
        return NodeChangeResponse(
            node_id=node_id,
            node_uuid=node.node_uuid,
            changed=False,
            summary="Node content is identical in the latest version.",
            old_hash=node.content_hash,
            new_hash=latest_node.content_hash,
            old_version_id=version.id,
            new_version_id=latest_version.id,
            old_version_number=version.version_number,
            new_version_number=latest_version.version_number,
            status=ChangeStatus.UNCHANGED,
        )

    return NodeChangeResponse(
        node_id=node_id,
        node_uuid=node.node_uuid,
        changed=True,
        summary=(
            f"Node was MODIFIED in version {latest_version.version_number}. "
            f"Hash changed from {node.content_hash[:12]}... "
            f"to {latest_node.content_hash[:12]}..."
        ),
        old_hash=node.content_hash,
        new_hash=latest_node.content_hash,
        old_version_id=version.id,
        new_version_id=latest_version.id,
        old_version_number=version.version_number,
        new_version_number=latest_version.version_number,
        status=ChangeStatus.MODIFIED,
    )


# ── Private helpers ────────────────────────────────────────────────────────────


def _get_version_or_404(db: Session, version_id: int) -> DocumentVersion:
    version = db.get(DocumentVersion, version_id)
    if version is None:
        raise NotFoundError(f"DocumentVersion {version_id} not found.")
    return version


def _load_nodes(db: Session, version_id: int) -> list[DocumentNode]:
    return (
        db.query(DocumentNode)
        .filter(DocumentNode.document_version_id == version_id)
        .all()
    )
