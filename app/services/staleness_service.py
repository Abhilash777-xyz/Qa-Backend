"""
app/services/staleness_service.py
───────────────────────────────────
Staleness detection for previously generated test cases.

When Version 2 of a document is ingested, this service is called to scan all
``GeneratedTestCaseMetadata`` rows and check whether the nodes they reference
have changed in the newest available version.

Detection logic:
  1. Load the metadata row's stored ``content_hashes_json``.
  2. Load the stored ``node_ids_json``.
  3. For each node_uuid, find the corresponding node in the LATEST version of
     the same document.
  4. Compare hashes.  If any differ → mark as stale.

The stale flag is a one-way ratchet: once stale, it is never automatically
un-set (a new generation would be required to produce a fresh result).
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.logging_config import get_logger
from app.models.document import DocumentNode, DocumentVersion
from app.models.generation import GeneratedTestCaseMetadata
from app.models.selection import Selection

logger = get_logger(__name__)


def run_staleness_check(db: Session, document_id: int) -> int:
    """
    Check all generations associated with a document for staleness.

    Called automatically after a new version is ingested.

    Args:
        db:          Active session.
        document_id: The document whose versions should be checked.

    Returns:
        Number of generation rows newly marked as stale.
    """
    # Find the latest version of this document.
    latest_version = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number.desc())
        .first()
    )
    if latest_version is None:
        return 0

    # Build a lookup: node_uuid → content_hash for the latest version.
    latest_nodes: dict[str, str] = {
        n.node_uuid: n.content_hash
        for n in db.query(DocumentNode)
        .filter(DocumentNode.document_version_id == latest_version.id)
        .all()
    }

    # Find all generations linked to this document (via selection → version).
    all_metas = (
        db.query(GeneratedTestCaseMetadata)
        .join(Selection, GeneratedTestCaseMetadata.selection_id == Selection.id)
        .join(
            DocumentVersion,
            Selection.document_version_id == DocumentVersion.id,
        )
        .filter(DocumentVersion.document_id == document_id)
        .filter(GeneratedTestCaseMetadata.is_stale.is_(False))
        .all()
    )

    newly_stale = 0
    for meta in all_metas:
        # Skip generations that are already at the latest version.
        if meta.document_version_id == latest_version.id:
            continue

        stored_node_ids: list[int] = json.loads(meta.node_ids_json)
        stored_hashes: list[str] = json.loads(meta.content_hashes_json)

        # Fetch node_uuids for the stored node_ids (from their original version).
        original_nodes = (
            db.query(DocumentNode)
            .filter(DocumentNode.id.in_(stored_node_ids))
            .all()
        )
        uuid_to_original_hash: dict[str, str] = {
            n.node_uuid: n.content_hash for n in original_nodes
        }

        changed_uuids: list[str] = []
        removed_uuids: list[str] = []

        for node_uuid, original_hash in uuid_to_original_hash.items():
            latest_hash = latest_nodes.get(node_uuid)
            if latest_hash is None:
                removed_uuids.append(node_uuid)
            elif latest_hash != original_hash:
                changed_uuids.append(node_uuid)

        if changed_uuids or removed_uuids:
            reasons: list[str] = []
            if changed_uuids:
                reasons.append(f"Modified nodes: {', '.join(changed_uuids[:5])}")
            if removed_uuids:
                reasons.append(f"Removed nodes: {', '.join(removed_uuids[:5])}")

            meta.is_stale = True
            meta.stale_reason = "; ".join(reasons)
            db.add(meta)
            newly_stale += 1
            logger.info(
                "Marked generation %d as stale: %s", meta.id, meta.stale_reason
            )

    logger.info(
        "Staleness check for document %d: %d/%d generations marked stale",
        document_id,
        newly_stale,
        len(all_metas),
    )
    return newly_stale


def check_generation_staleness(db: Session, generation_id: int) -> GeneratedTestCaseMetadata:
    """
    Return the metadata row for a generation, computing staleness on-demand
    if it has not been computed yet.

    Args:
        db:            Active session.
        generation_id: ID of the GeneratedTestCaseMetadata row.

    Returns:
        The (potentially updated) metadata row.

    Raises:
        NotFoundError if the generation does not exist.
    """
    from app.core.exceptions import NotFoundError

    meta = db.get(GeneratedTestCaseMetadata, generation_id)
    if meta is None:
        raise NotFoundError(f"Generation {generation_id} not found.")

    if not meta.is_stale:
        # Run a fresh on-demand check.
        selection = db.get(Selection, meta.selection_id)
        if selection:
            version = db.get(DocumentVersion, selection.document_version_id)
            if version:
                run_staleness_check(db, version.document_id)
                db.refresh(meta)

    return meta
