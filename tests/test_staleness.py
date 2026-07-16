"""
tests/test_staleness.py
────────────────────────
Tests for the staleness detection service.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import Session

from app.models.document import Document, DocumentNode, DocumentVersion
from app.models.generation import GeneratedTestCaseMetadata
from app.models.selection import Selection
from app.parser.utils import compute_content_hash
from app.services.staleness_service import run_staleness_check


@pytest.fixture
def staleness_setup(db: Session, tmp_path):
    """
    Set up a document with two versions, a selection pinned to v1,
    and a generation metadata row referencing v1 nodes.

    Returns (doc_id, v1_id, v2_id, selection_id, metadata_id, v1_node_ids).
    """
    doc = Document(title="Stale Doc", file_name="staledoc")
    db.add(doc)
    db.flush()

    v1 = DocumentVersion(document_id=doc.id, version_number=1, file_hash="h1")
    db.add(v1)
    db.flush()

    v1_nodes = []
    for uuid, title, num in [
        ("1", "Safety", "1"),
        ("2", "Electrical", "2"),
    ]:
        n = DocumentNode(
            document_version_id=v1.id,
            node_uuid=uuid,
            heading_level=1,
            numbering=num,
            title=title,
            node_type="section",
            body_text=f"Original body for {title}.",
            content_hash=compute_content_hash(title, num, f"Original body for {title}."),
        )
        db.add(n)
        db.flush()
        v1_nodes.append(n)

    sel = Selection(
        name="Safety Selection",
        document_version_id=v1.id,
        nodes=v1_nodes,
    )
    db.add(sel)
    db.flush()

    # Create a fake output file.
    output_file = tmp_path / "gen_test.json"
    output_file.write_text("{}")

    meta = GeneratedTestCaseMetadata(
        selection_id=sel.id,
        document_version_id=v1.id,
        node_ids_json=json.dumps([n.id for n in v1_nodes]),
        content_hashes_json=json.dumps([n.content_hash for n in v1_nodes]),
        llm_provider="openrouter",
        llm_model="test-model",
        prompt_version="v1",
        output_file_path=str(output_file),
        is_stale=False,
        stale_reason="",
    )
    db.add(meta)
    db.flush()

    return doc.id, v1.id, v1_nodes, sel.id, meta.id


class TestStalenessDetection:
    def test_not_stale_when_no_v2(self, db: Session, staleness_setup):
        doc_id, v1_id, v1_nodes, sel_id, meta_id = staleness_setup
        newly_stale = run_staleness_check(db, doc_id)
        assert newly_stale == 0
        meta = db.get(GeneratedTestCaseMetadata, meta_id)
        assert meta.is_stale is False

    def test_not_stale_when_v2_has_same_content(self, db: Session, staleness_setup):
        doc_id, v1_id, v1_nodes, sel_id, meta_id = staleness_setup

        # Create v2 with identical content.
        v2 = DocumentVersion(document_id=doc_id, version_number=2, file_hash="h2")
        db.add(v2)
        db.flush()

        for n in v1_nodes:
            db.add(
                DocumentNode(
                    document_version_id=v2.id,
                    node_uuid=n.node_uuid,
                    heading_level=n.heading_level,
                    numbering=n.numbering,
                    title=n.title,
                    node_type=n.node_type,
                    body_text=n.body_text,  # Same body.
                    content_hash=n.content_hash,  # Same hash.
                )
            )
        db.flush()

        newly_stale = run_staleness_check(db, doc_id)
        assert newly_stale == 0
        meta = db.get(GeneratedTestCaseMetadata, meta_id)
        assert meta.is_stale is False

    def test_stale_when_node_body_changed(self, db: Session, staleness_setup):
        doc_id, v1_id, v1_nodes, sel_id, meta_id = staleness_setup

        v2 = DocumentVersion(document_id=doc_id, version_number=2, file_hash="h2")
        db.add(v2)
        db.flush()

        for n in v1_nodes:
            new_body = f"UPDATED body for {n.title}."
            db.add(
                DocumentNode(
                    document_version_id=v2.id,
                    node_uuid=n.node_uuid,
                    heading_level=n.heading_level,
                    numbering=n.numbering,
                    title=n.title,
                    node_type=n.node_type,
                    body_text=new_body,
                    content_hash=compute_content_hash(n.title, n.numbering, new_body),
                )
            )
        db.flush()

        newly_stale = run_staleness_check(db, doc_id)
        assert newly_stale == 1
        meta = db.get(GeneratedTestCaseMetadata, meta_id)
        assert meta.is_stale is True
        assert meta.stale_reason != ""

    def test_stale_when_node_removed(self, db: Session, staleness_setup):
        doc_id, v1_id, v1_nodes, sel_id, meta_id = staleness_setup

        v2 = DocumentVersion(document_id=doc_id, version_number=2, file_hash="h2")
        db.add(v2)
        db.flush()

        # Only add the FIRST v1 node to v2 — the second is "removed".
        n = v1_nodes[0]
        db.add(
            DocumentNode(
                document_version_id=v2.id,
                node_uuid=n.node_uuid,
                heading_level=n.heading_level,
                numbering=n.numbering,
                title=n.title,
                node_type=n.node_type,
                body_text=n.body_text,
                content_hash=n.content_hash,
            )
        )
        db.flush()

        newly_stale = run_staleness_check(db, doc_id)
        assert newly_stale == 1
        meta = db.get(GeneratedTestCaseMetadata, meta_id)
        assert meta.is_stale is True
        assert "Removed" in meta.stale_reason

    def test_already_stale_not_double_counted(self, db: Session, staleness_setup):
        doc_id, v1_id, v1_nodes, sel_id, meta_id = staleness_setup

        # Pre-mark as stale.
        meta = db.get(GeneratedTestCaseMetadata, meta_id)
        meta.is_stale = True
        meta.stale_reason = "Already stale"
        db.flush()

        v2 = DocumentVersion(document_id=doc_id, version_number=2, file_hash="h2")
        db.add(v2)
        db.flush()

        # run_staleness_check skips already-stale rows.
        newly_stale = run_staleness_check(db, doc_id)
        assert newly_stale == 0
