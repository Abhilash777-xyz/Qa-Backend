"""
tests/conftest.py
──────────────────
Shared pytest fixtures for the test suite.

Fixtures provided:
  - ``engine``       – In-memory SQLite engine (isolated per-test session).
  - ``db``           – Fresh database session with rollback after each test.
  - ``client``       – FastAPI TestClient with the in-memory DB wired in.
  - ``sample_nodes`` – Pre-built ParsedNode tree for parser tests.
  - ``version1_id``  – ID of a seeded Version 1 in the test database.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.database.base import Base
from app.database.session import get_db
from app.main import app
from app.models.document import Document, DocumentNode, DocumentVersion
from app.models.generation import GeneratedTestCaseMetadata
from app.models.selection import Selection, SelectionNode
from app.parser.models import ParsedNode
from app.parser.utils import compute_content_hash

# ── In-memory database ─────────────────────────────────────────────────────────

TEST_DATABASE_URL = "sqlite:///:memory:"


@pytest.fixture(scope="session")
def engine():
    """Create a shared in-memory SQLite engine for the test session."""
    _engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=_engine)
    yield _engine
    _engine.dispose()


@pytest.fixture(scope="function")
def db(engine) -> Session:
    """
    Yield a fresh database session for each test.
    All changes are rolled back after the test completes.
    """
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture(scope="function")
def client(db: Session) -> TestClient:
    """
    FastAPI TestClient with the in-memory DB dependency overridden.
    """
    def _override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ── Sample data helpers ────────────────────────────────────────────────────────


def make_node(
    node_uuid: str,
    title: str,
    heading_level: int = 1,
    numbering: str = "",
    body_text: str = "",
    node_type: str = "section",
) -> ParsedNode:
    """Build a ParsedNode with a pre-computed content hash."""
    return ParsedNode(
        node_uuid=node_uuid,
        title=title,
        heading_level=heading_level,
        numbering=numbering,
        body_text=body_text,
        node_type=node_type,
        content_hash=compute_content_hash(title, numbering, body_text),
    )


@pytest.fixture
def sample_tree() -> ParsedNode:
    """
    Return a small but realistic ParsedNode tree:

    Root
     ├── 1 Introduction
     │    └── 1.1 Overview
     ├── 2 Safety Requirements
     │    ├── 2.1 General Safety
     │    │    └── [list node]
     │    └── 2.2 Electrical Safety
     │         └── [table node]
     └── 3 Specifications
    """
    root = make_node("root", "Document Root", heading_level=0)

    s1 = make_node("1", "Introduction", heading_level=1, numbering="1", body_text="Introduction text.")
    s1_1 = make_node("1.1", "Overview", heading_level=2, numbering="1.1", body_text="Overview details.")
    s1.add_child(s1_1)

    s2 = make_node("2", "Safety Requirements", heading_level=1, numbering="2", body_text="Safety intro.")
    s2_1 = make_node("2.1", "General Safety", heading_level=2, numbering="2.1", body_text="General safety rules.")
    list_node = make_node(
        "2.1:list:0",
        "",
        heading_level=3,
        body_text='["Wear PPE", "Check voltage"]',
        node_type="list",
    )
    s2_1.add_child(list_node)
    s2_2 = make_node("2.2", "Electrical Safety", heading_level=2, numbering="2.2", body_text="Electrical rules.")
    table_node = make_node(
        "2.2:table:0",
        "",
        heading_level=3,
        body_text='[["Parameter", "Value"], ["Voltage", "24V"]]',
        node_type="table",
    )
    s2_2.add_child(table_node)
    s2.add_child(s2_1)
    s2.add_child(s2_2)

    s3 = make_node("3", "Specifications", heading_level=1, numbering="3", body_text="Technical specs.")

    root.add_child(s1)
    root.add_child(s2)
    root.add_child(s3)

    return root


@pytest.fixture
def seeded_version(db: Session) -> tuple[int, int]:
    """
    Seed the test database with one Document and one DocumentVersion
    containing 3 top-level nodes.

    Returns:
        (document_id, version_id)
    """
    doc = Document(title="CT200 Manual", file_name="ct200_manual")
    db.add(doc)
    db.flush()

    version = DocumentVersion(
        document_id=doc.id,
        version_number=1,
        file_hash="abc123def456",
    )
    db.add(version)
    db.flush()

    nodes_data = [
        ("1", "Introduction", 1, "1", "Intro body text."),
        ("2", "Safety Requirements", 1, "2", "Safety body text."),
        ("3", "Specifications", 1, "3", "Specifications body."),
    ]
    for uuid, title, level, numbering, body in nodes_data:
        db.add(
            DocumentNode(
                document_version_id=version.id,
                node_uuid=uuid,
                parent_id=None,
                heading_level=level,
                numbering=numbering,
                title=title,
                node_type="section",
                body_text=body,
                content_hash=compute_content_hash(title, numbering, body),
            )
        )
    db.flush()

    return doc.id, version.id
