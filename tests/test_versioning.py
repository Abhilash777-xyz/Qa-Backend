"""
tests/test_versioning.py
─────────────────────────
Tests for the version comparison service.

Tests:
  - All nodes unchanged between identical versions.
  - MODIFIED detection when body_text differs.
  - ADDED detection for new nodes in v2.
  - REMOVED detection for nodes absent from v2.
  - Hash stability across re-ingestion.
  - Summary counts match diff list.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models.document import Document, DocumentNode, DocumentVersion
from app.parser.utils import compute_content_hash
from app.schemas.diff import ChangeStatus
from app.services.version_service import compare_versions, get_node_change


def _make_version(db: Session, document_id: int, version_number: int) -> DocumentVersion:
    v = DocumentVersion(
        document_id=document_id,
        version_number=version_number,
        file_hash=f"hash-v{version_number}",
    )
    db.add(v)
    db.flush()
    return v


def _add_node(
    db: Session,
    version_id: int,
    uuid: str,
    title: str,
    body: str = "",
    numbering: str = "",
    parent_id: int | None = None,
) -> DocumentNode:
    n = DocumentNode(
        document_version_id=version_id,
        node_uuid=uuid,
        parent_id=parent_id,
        heading_level=1,
        numbering=numbering,
        title=title,
        node_type="section",
        body_text=body,
        content_hash=compute_content_hash(title, numbering, body),
    )
    db.add(n)
    db.flush()
    return n


@pytest.fixture
def two_version_doc(db: Session):
    doc = Document(title="Test Doc", file_name="testdoc")
    db.add(doc)
    db.flush()
    return doc


class TestVersionComparison:
    def test_all_unchanged_when_identical(self, db: Session, two_version_doc):
        v1 = _make_version(db, two_version_doc.id, 1)
        v2 = _make_version(db, two_version_doc.id, 2)

        for uuid, title, num in [("1", "Intro", "1"), ("2", "Safety", "2")]:
            _add_node(db, v1.id, uuid, title, "body", num)
            _add_node(db, v2.id, uuid, title, "body", num)

        result = compare_versions(db, v1.id, v2.id)
        assert result.unchanged == 2
        assert result.modified == 0
        assert result.added == 0
        assert result.removed == 0

    def test_modified_detected(self, db: Session, two_version_doc):
        v1 = _make_version(db, two_version_doc.id, 1)
        v2 = _make_version(db, two_version_doc.id, 2)

        _add_node(db, v1.id, "1", "Safety", "Original body", "1")
        _add_node(db, v2.id, "1", "Safety", "Updated body content", "1")

        result = compare_versions(db, v1.id, v2.id)
        assert result.modified == 1
        modified_diff = next(d for d in result.diffs if d.status == ChangeStatus.MODIFIED)
        assert modified_diff.node_uuid == "1"
        assert modified_diff.v1_hash != modified_diff.v2_hash

    def test_added_detected(self, db: Session, two_version_doc):
        v1 = _make_version(db, two_version_doc.id, 1)
        v2 = _make_version(db, two_version_doc.id, 2)

        _add_node(db, v1.id, "1", "Intro", "body", "1")
        _add_node(db, v2.id, "1", "Intro", "body", "1")
        _add_node(db, v2.id, "2", "New Section", "new body", "2")  # Only in v2.

        result = compare_versions(db, v1.id, v2.id)
        assert result.added == 1
        added_diff = next(d for d in result.diffs if d.status == ChangeStatus.ADDED)
        assert added_diff.node_uuid == "2"
        assert added_diff.v1_node_id is None
        assert added_diff.v2_node_id is not None

    def test_removed_detected(self, db: Session, two_version_doc):
        v1 = _make_version(db, two_version_doc.id, 1)
        v2 = _make_version(db, two_version_doc.id, 2)

        _add_node(db, v1.id, "1", "Intro", "body", "1")
        _add_node(db, v1.id, "2", "Old Section", "old body", "2")  # Only in v1.
        _add_node(db, v2.id, "1", "Intro", "body", "1")

        result = compare_versions(db, v1.id, v2.id)
        assert result.removed == 1
        removed_diff = next(d for d in result.diffs if d.status == ChangeStatus.REMOVED)
        assert removed_diff.node_uuid == "2"

    def test_summary_counts_match_diffs(self, db: Session, two_version_doc):
        v1 = _make_version(db, two_version_doc.id, 1)
        v2 = _make_version(db, two_version_doc.id, 2)

        _add_node(db, v1.id, "1", "Same", "body", "1")
        _add_node(db, v2.id, "1", "Same", "body", "1")  # unchanged
        _add_node(db, v1.id, "2", "Modified", "old", "2")
        _add_node(db, v2.id, "2", "Modified", "new", "2")  # modified
        _add_node(db, v2.id, "3", "Added", "new", "3")    # added
        _add_node(db, v1.id, "4", "Removed", "old", "4")  # removed

        result = compare_versions(db, v1.id, v2.id)
        assert result.unchanged == 1
        assert result.modified == 1
        assert result.added == 1
        assert result.removed == 1
        assert result.total_nodes == 4
        assert len(result.diffs) == 4

    def test_hash_stability(self):
        """Same inputs must always produce the same hash."""
        h1 = compute_content_hash("Safety Requirements", "2.1", "All wiring must be insulated.")
        h2 = compute_content_hash("Safety Requirements", "2.1", "All wiring must be insulated.")
        assert h1 == h2

    def test_node_change_no_newer_version(self, db: Session, two_version_doc):
        v1 = _make_version(db, two_version_doc.id, 1)
        node = _add_node(db, v1.id, "1", "Intro", "body", "1")

        result = get_node_change(db, node.id)
        assert result.changed is False
        assert result.status == ChangeStatus.UNCHANGED

    def test_node_change_modified(self, db: Session, two_version_doc):
        v1 = _make_version(db, two_version_doc.id, 1)
        v2 = _make_version(db, two_version_doc.id, 2)

        n1 = _add_node(db, v1.id, "1", "Safety", "original content", "1")
        _add_node(db, v2.id, "1", "Safety", "updated content", "1")

        result = get_node_change(db, n1.id)
        assert result.changed is True
        assert result.status == ChangeStatus.MODIFIED

    def test_node_change_removed(self, db: Session, two_version_doc):
        v1 = _make_version(db, two_version_doc.id, 1)
        v2 = _make_version(db, two_version_doc.id, 2)

        n1 = _add_node(db, v1.id, "99", "Removed Section", "body", "99")
        # Do NOT add uuid "99" to v2.

        result = get_node_change(db, n1.id)
        assert result.changed is True
        assert result.status == ChangeStatus.REMOVED
