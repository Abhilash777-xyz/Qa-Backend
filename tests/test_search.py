"""
tests/test_search.py
─────────────────────
Tests for the search service.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models.document import Document, DocumentNode, DocumentVersion
from app.parser.utils import compute_content_hash
from app.services.search_service import search_nodes, _make_snippet


@pytest.fixture
def searchable_db(db: Session):
    """Seed the DB with a set of nodes for search testing."""
    doc = Document(title="Search Doc", file_name="searchdoc")
    db.add(doc)
    db.flush()

    version = DocumentVersion(
        document_id=doc.id, version_number=1, file_hash="abc"
    )
    db.add(version)
    db.flush()

    data = [
        ("1", "Safety Requirements", 1, "1", "All safety interlocks must be tested."),
        ("2", "Electrical Safety", 2, "1.1", "Voltage levels must not exceed 24V."),
        ("3", "Mechanical Safety", 2, "1.2", "Guards must be in place before operation."),
        ("4", "Specifications", 1, "2", "Technical specifications and tolerances."),
        ("5", "Calibration Procedure", 1, "3", "Steps for sensor calibration and alignment."),
    ]
    for uuid, title, level, num, body in data:
        db.add(
            DocumentNode(
                document_version_id=version.id,
                node_uuid=uuid,
                heading_level=level,
                numbering=num,
                title=title,
                node_type="section",
                body_text=body,
                content_hash=compute_content_hash(title, num, body),
            )
        )
    db.flush()
    return version.id


class TestSearchService:
    def test_heading_match(self, db: Session, searchable_db):
        result = search_nodes(db, "safety")
        titles = [r.title for r in result.results]
        assert "Safety Requirements" in titles
        assert "Electrical Safety" in titles
        assert "Mechanical Safety" in titles

    def test_body_match(self, db: Session, searchable_db):
        result = search_nodes(db, "calibration")
        assert result.total >= 1
        # Body match should include the Calibration Procedure node.
        assert any("Calibration" in r.title for r in result.results)

    def test_partial_match(self, db: Session, searchable_db):
        result = search_nodes(db, "volt")
        assert result.total >= 1
        # "volt" is in body_text of Electrical Safety.
        assert any("Electrical" in r.title for r in result.results)

    def test_case_insensitive(self, db: Session, searchable_db):
        result_lower = search_nodes(db, "safety")
        result_upper = search_nodes(db, "SAFETY")
        assert result_lower.total == result_upper.total

    def test_title_matches_ranked_before_body(self, db: Session, searchable_db):
        result = search_nodes(db, "safety")
        # Title matches should appear before body matches in results list.
        title_positions = [
            i for i, r in enumerate(result.results) if r.match_in == "title"
        ]
        body_positions = [
            i for i, r in enumerate(result.results) if r.match_in == "body"
        ]
        if title_positions and body_positions:
            assert min(title_positions) < max(body_positions)

    def test_version_filter(self, db: Session, searchable_db):
        result = search_nodes(db, "safety", version_id=searchable_db)
        assert result.total >= 1
        assert all(r.document_version_id == searchable_db for r in result.results)

    def test_no_results_for_unknown_term(self, db: Session, searchable_db):
        result = search_nodes(db, "xyzzyplugh123")
        assert result.total == 0
        assert result.results == []

    def test_empty_query_returns_empty(self, db: Session, searchable_db):
        result = search_nodes(db, "")
        assert result.total == 0

    def test_limit_respected(self, db: Session, searchable_db):
        result = search_nodes(db, "a", limit=2)
        assert len(result.results) <= 2


class TestSnippet:
    def test_snippet_contains_term(self):
        body = "The system voltage must not exceed 24V under any circumstances."
        snippet = _make_snippet(body, "voltage")
        assert "voltage" in snippet.lower()

    def test_snippet_truncated_long_body(self):
        body = "x " * 500
        snippet = _make_snippet(body, "x")
        assert len(snippet) <= 210  # max + ellipsis chars

    def test_snippet_no_match_returns_start(self):
        body = "This is a long body text with no matching term inside it at all."
        snippet = _make_snippet(body, "zzzz")
        assert snippet == body[:200]

    def test_snippet_empty_body(self):
        assert _make_snippet("", "anything") == ""
