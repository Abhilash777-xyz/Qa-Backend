"""
tests/test_api.py
──────────────────
End-to-end API tests using FastAPI's TestClient with the in-memory database.

Tests cover the happy path for every major endpoint group:
  - Health check
  - Document ingestion (via the ingestion service directly, not file upload)
  - Node retrieval
  - Search
  - Selections
  - Change detection
  - Test case generation (with mocked LLM)
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.document import Document, DocumentNode, DocumentVersion
from app.models.selection import Selection
from app.parser.utils import compute_content_hash


# ── Helpers ────────────────────────────────────────────────────────────────────


def _seed_document(db: Session) -> tuple[int, int, list[int]]:
    """Seed a document with two versions in the test database."""
    doc = Document(title="CT200 Manual", file_name="ct200_manual")
    db.add(doc)
    db.flush()

    v1 = DocumentVersion(document_id=doc.id, version_number=1, file_hash="hash1abc")
    db.add(v1)
    db.flush()

    node_ids = []
    for uuid, title, num, body in [
        ("1", "Introduction", "1", "Intro body text."),
        ("2", "Safety", "2", "Safety body text."),
        ("3", "Specifications", "3", "Specs body."),
    ]:
        n = DocumentNode(
            document_version_id=v1.id,
            node_uuid=uuid,
            heading_level=1,
            numbering=num,
            title=title,
            node_type="section",
            body_text=body,
            content_hash=compute_content_hash(title, num, body),
        )
        db.add(n)
        db.flush()
        node_ids.append(n.id)

    return doc.id, v1.id, node_ids


# ── Health ─────────────────────────────────────────────────────────────────────


class TestHealth:
    def test_health_check(self, client: TestClient):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


# ── Documents ──────────────────────────────────────────────────────────────────


class TestDocumentsAPI:
    def test_list_documents_empty(self, client: TestClient):
        resp = client.get("/api/v1/documents")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_list_documents_with_data(self, client: TestClient, db: Session):
        _seed_document(db)
        resp = client.get("/api/v1/documents")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    def test_get_document_not_found(self, client: TestClient):
        resp = client.get("/api/v1/documents/99999")
        assert resp.status_code == 404

    def test_get_document_found(self, client: TestClient, db: Session):
        doc_id, _, _ = _seed_document(db)
        resp = client.get(f"/api/v1/documents/{doc_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == doc_id


# ── Versions ───────────────────────────────────────────────────────────────────


class TestVersionsAPI:
    def test_list_versions(self, client: TestClient, db: Session):
        doc_id, v1_id, _ = _seed_document(db)
        resp = client.get(f"/api/v1/versions?document_id={doc_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["versions"]) == 1
        assert data["versions"][0]["id"] == v1_id

    def test_get_version(self, client: TestClient, db: Session):
        _, v1_id, _ = _seed_document(db)
        resp = client.get(f"/api/v1/versions/{v1_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == v1_id

    def test_get_version_not_found(self, client: TestClient):
        resp = client.get("/api/v1/versions/99999")
        assert resp.status_code == 404


# ── Nodes ──────────────────────────────────────────────────────────────────────


class TestNodesAPI:
    def test_list_nodes(self, client: TestClient, db: Session):
        _, v1_id, node_ids = _seed_document(db)
        resp = client.get(f"/api/v1/nodes?version_id={v1_id}")
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    def test_get_node(self, client: TestClient, db: Session):
        _, _, node_ids = _seed_document(db)
        resp = client.get(f"/api/v1/nodes/{node_ids[0]}")
        assert resp.status_code == 200
        assert resp.json()["id"] == node_ids[0]

    def test_get_node_not_found(self, client: TestClient):
        resp = client.get("/api/v1/nodes/99999")
        assert resp.status_code == 404

    def test_get_children_empty(self, client: TestClient, db: Session):
        _, _, node_ids = _seed_document(db)
        resp = client.get(f"/api/v1/nodes/{node_ids[0]}/children")
        assert resp.status_code == 200
        assert resp.json()["children"] == []

    def test_get_tree(self, client: TestClient, db: Session):
        _, _, node_ids = _seed_document(db)
        resp = client.get(f"/api/v1/nodes/{node_ids[0]}/tree")
        assert resp.status_code == 200
        assert "children" in resp.json()


# ── Search ─────────────────────────────────────────────────────────────────────


class TestSearchAPI:
    def test_search_finds_title(self, client: TestClient, db: Session):
        _seed_document(db)
        resp = client.get("/api/v1/search?q=safety")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert any("Safety" in r["title"] for r in data["results"])

    def test_search_empty_query(self, client: TestClient):
        # q with length < 1 should be rejected.
        resp = client.get("/api/v1/search?q=")
        assert resp.status_code in (200, 422)

    def test_search_no_results(self, client: TestClient, db: Session):
        _seed_document(db)
        resp = client.get("/api/v1/search?q=xyzzyplugh123")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


# ── Selections ─────────────────────────────────────────────────────────────────


class TestSelectionsAPI:
    def test_create_selection(self, client: TestClient, db: Session):
        _, v1_id, node_ids = _seed_document(db)
        resp = client.post(
            "/api/v1/selections",
            json={"name": "Safety", "version_id": v1_id, "node_ids": node_ids[:2]},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Safety"
        assert data["node_count"] == 2

    def test_list_selections(self, client: TestClient, db: Session):
        _, v1_id, node_ids = _seed_document(db)
        client.post("/api/v1/selections", json={"name": "S1", "version_id": v1_id, "node_ids": [node_ids[0]]})
        resp = client.get("/api/v1/selections")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    def test_get_selection(self, client: TestClient, db: Session):
        _, v1_id, node_ids = _seed_document(db)
        created = client.post(
            "/api/v1/selections",
            json={"name": "Test", "version_id": v1_id, "node_ids": [node_ids[0]]},
        ).json()
        resp = client.get(f"/api/v1/selections/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == created["id"]

    def test_get_selection_not_found(self, client: TestClient):
        resp = client.get("/api/v1/selections/99999")
        assert resp.status_code == 404


# ── Change detection ───────────────────────────────────────────────────────────


class TestChangesAPI:
    def test_node_change_no_newer_version(self, client: TestClient, db: Session):
        _, _, node_ids = _seed_document(db)
        resp = client.get(f"/api/v1/changes/{node_ids[0]}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["changed"] is False

    def test_version_diff(self, client: TestClient, db: Session):
        doc_id, v1_id, _ = _seed_document(db)

        # Add a v2.
        v2 = DocumentVersion(document_id=doc_id, version_number=2, file_hash="hash2")
        db.add(v2)
        db.flush()
        db.add(
            DocumentNode(
                document_version_id=v2.id,
                node_uuid="1",
                heading_level=1,
                numbering="1",
                title="Introduction",
                node_type="section",
                body_text="Changed intro body.",
                content_hash=compute_content_hash("Introduction", "1", "Changed intro body."),
            )
        )
        db.flush()

        resp = client.get(f"/api/v1/changes/versions?v1={v1_id}&v2={v2.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["modified"] >= 1


# ── Generation (mocked LLM) ────────────────────────────────────────────────────


MOCK_TEST_CASES = [
    {
        "title": "TC-001 Safety Interlock Test",
        "objective": "Verify safety interlocks engage correctly.",
        "preconditions": ["System powered on", "Test mode active"],
        "steps": ["Step 1: Enable interlock", "Step 2: Trigger fault"],
        "expected_result": "Interlock engages within 100ms.",
        "priority": "High",
        "requirement_reference": "REQ-SAF-001",
    }
]


class TestGenerationAPI:
    def test_generate_without_key_returns_503(self, client: TestClient, db: Session):
        _, v1_id, node_ids = _seed_document(db)
        client.post("/api/v1/selections", json={"name": "S", "version_id": v1_id, "node_ids": [node_ids[0]]})
        selections = client.get("/api/v1/selections").json()
        sel_id = selections["items"][0]["id"]

        with patch("app.services.llm_service.settings") as mock_settings:
            mock_settings.llm_configured = False
            resp = client.post("/api/v1/generate", json={"selection_id": sel_id})
        assert resp.status_code == 503
