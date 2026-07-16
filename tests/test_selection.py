"""
tests/test_selection.py
────────────────────────
Tests for the selection service, focusing on version-pinning.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, ValidationError
from app.models.document import Document, DocumentNode, DocumentVersion
from app.parser.utils import compute_content_hash
from app.schemas.selection import SelectionCreate
from app.services.selection_service import (
    create_selection,
    get_selection,
    get_selection_nodes,
    list_selections,
)


@pytest.fixture
def two_version_setup(db: Session):
    """
    Seed two versions of the same document.
    Returns (doc_id, v1_id, v2_id, v1_node_ids, v2_node_ids).
    """
    doc = Document(title="CT200 Manual", file_name="ct200_manual")
    db.add(doc)
    db.flush()

    v1 = DocumentVersion(document_id=doc.id, version_number=1, file_hash="hash1")
    v2 = DocumentVersion(document_id=doc.id, version_number=2, file_hash="hash2")
    db.add(v1)
    db.add(v2)
    db.flush()

    v1_nodes = []
    for uuid, title, num in [("1", "Intro", "1"), ("2", "Safety", "2"), ("3", "Specs", "3")]:
        n = DocumentNode(
            document_version_id=v1.id,
            node_uuid=uuid,
            heading_level=1,
            numbering=num,
            title=title,
            node_type="section",
            body_text=f"{title} body v1.",
            content_hash=compute_content_hash(title, num, f"{title} body v1."),
        )
        db.add(n)
        db.flush()
        v1_nodes.append(n)

    v2_nodes = []
    for uuid, title, num in [("1", "Intro", "1"), ("2", "Safety Updated", "2"), ("4", "New Section", "4")]:
        n = DocumentNode(
            document_version_id=v2.id,
            node_uuid=uuid,
            heading_level=1,
            numbering=num,
            title=title,
            node_type="section",
            body_text=f"{title} body v2.",
            content_hash=compute_content_hash(title, num, f"{title} body v2."),
        )
        db.add(n)
        db.flush()
        v2_nodes.append(n)

    return doc.id, v1.id, v2.id, [n.id for n in v1_nodes], [n.id for n in v2_nodes]


class TestSelectionCreation:
    def test_create_basic(self, db: Session, two_version_setup):
        _, v1_id, _, v1_node_ids, _ = two_version_setup
        payload = SelectionCreate(
            name="Safety",
            description="Safety nodes",
            version_id=v1_id,
            node_ids=v1_node_ids[:2],
        )
        result = create_selection(db, payload)
        assert result.id is not None
        assert result.name == "Safety"
        assert result.document_version_id == v1_id
        assert result.node_count == 2

    def test_create_deduplicates_nodes(self, db: Session, two_version_setup):
        _, v1_id, _, v1_node_ids, _ = two_version_setup
        node_id = v1_node_ids[0]
        payload = SelectionCreate(
            name="Dedup Test",
            version_id=v1_id,
            node_ids=[node_id, node_id, node_id],  # Duplicates.
        )
        result = create_selection(db, payload)
        assert result.node_count == 1

    def test_create_with_wrong_version_node_raises(self, db: Session, two_version_setup):
        _, v1_id, v2_id, _, v2_node_ids = two_version_setup
        payload = SelectionCreate(
            name="Bad Selection",
            version_id=v1_id,
            node_ids=[v2_node_ids[0]],  # v2 node in v1 selection.
        )
        with pytest.raises(ValidationError):
            create_selection(db, payload)

    def test_create_with_nonexistent_version_raises(self, db: Session):
        payload = SelectionCreate(
            name="Bad", version_id=99999, node_ids=[1]
        )
        with pytest.raises(NotFoundError):
            create_selection(db, payload)

    def test_create_with_nonexistent_node_raises(self, db: Session, two_version_setup):
        _, v1_id, _, _, _ = two_version_setup
        payload = SelectionCreate(
            name="Bad", version_id=v1_id, node_ids=[99999]
        )
        with pytest.raises(NotFoundError):
            create_selection(db, payload)


class TestVersionPinning:
    def test_selection_pinned_to_v1_nodes(self, db: Session, two_version_setup):
        """
        Core test: after v2 is imported, a v1-pinned selection must still
        resolve to v1 nodes only.
        """
        _, v1_id, v2_id, v1_node_ids, _ = two_version_setup

        # Create selection pinned to v1.
        payload = SelectionCreate(
            name="Safety v1",
            version_id=v1_id,
            node_ids=v1_node_ids,
        )
        sel = create_selection(db, payload)

        # Retrieve the selection — must still return v1 nodes.
        detail = get_selection(db, sel.id)
        resolved_version_ids = {n.document_version_id for n in detail.nodes}
        assert resolved_version_ids == {v1_id}, (
            "Selection should only resolve nodes from the pinned v1, not v2"
        )

    def test_selection_node_bodies_are_v1_content(self, db: Session, two_version_setup):
        """Node body text from the selection must be the v1 content."""
        _, v1_id, _, v1_node_ids, _ = two_version_setup
        payload = SelectionCreate(
            name="Content Check",
            version_id=v1_id,
            node_ids=[v1_node_ids[1]],  # "Safety" node from v1.
        )
        sel = create_selection(db, payload)
        nodes = get_selection_nodes(db, sel.id)
        assert all("v1" in n.body_text for n in nodes)

    def test_list_selections(self, db: Session, two_version_setup):
        _, v1_id, _, v1_node_ids, _ = two_version_setup
        for name in ["Alpha", "Beta", "Gamma"]:
            create_selection(db, SelectionCreate(name=name, version_id=v1_id, node_ids=[v1_node_ids[0]]))

        result = list_selections(db)
        assert result.total >= 3

    def test_get_nonexistent_selection_raises(self, db: Session):
        with pytest.raises(NotFoundError):
            get_selection(db, 99999)
