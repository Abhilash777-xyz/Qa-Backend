"""
app/services/ingestion_service.py
──────────────────────────────────
Orchestrates end-to-end PDF ingestion:
  1. Compute the PDF file hash (for deduplication / DocumentVersion.file_hash).
  2. Look up or create the parent Document record.
  3. Check whether this version already exists (idempotent re-ingestion guard).
  4. Call the PDF parser.
  5. Persist the node tree to the database in a single bulk insert.
  6. Return an IngestionResponse summary.

Design decisions:
  - The parser is called outside any DB transaction so that a parse failure
    does not leave orphaned rows.
  - Nodes are inserted in pre-order (parent before child) using a single
    Session.add_all() call for performance.
  - ``id`` assignments happen after flush so we can wire parent_id FKs.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, IngestionError
from app.core.logging_config import get_logger
from app.models.document import Document, DocumentNode, DocumentVersion
from app.parser.models import ParsedNode
from app.parser.pdf_parser import parse_pdf
from app.parser.utils import compute_file_hash
from app.schemas.document import IngestionResponse

logger = get_logger(__name__)


def ingest_pdf(
    db: Session,
    pdf_path: Path,
    version_number: int,
    document_title: str | None = None,
) -> IngestionResponse:
    """
    Parse a PDF and persist the full document tree to the database.

    Args:
        db:               Active SQLAlchemy session.
        pdf_path:         Path to the PDF file on disk.
        version_number:   1-based version number to assign.
        document_title:   Override the document title (defaults to file stem).

    Returns:
        IngestionResponse with counts and IDs.

    Raises:
        IngestionError:  On parse failure or unexpected DB error.
        ConflictError:   If this (document, version_number) already exists.
    """
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.exists():
        raise IngestionError(f"PDF file not found: {pdf_path}")

    # ── Compute file hash ───────────────────────────────────────────────────
    raw_bytes = pdf_path.read_bytes()
    file_hash = compute_file_hash(raw_bytes)
    logger.info("Ingesting %s (hash=%s, version=%d)", pdf_path.name, file_hash[:12], version_number)

    # ── Resolve Document record ─────────────────────────────────────────────
    title = document_title or pdf_path.stem.replace("_", " ").title()
    # Use file stem without version suffix as a stable document key.
    base_name = _normalise_document_name(pdf_path.stem)

    document = db.query(Document).filter(Document.file_name == base_name).first()
    if document is None:
        document = Document(title=title, file_name=base_name)
        db.add(document)
        db.flush()  # Obtain document.id before referencing it.
        logger.info("Created Document id=%d title=%r", document.id, document.title)

    # ── Guard: prevent duplicate version ───────────────────────────────────
    existing_version = (
        db.query(DocumentVersion)
        .filter(
            DocumentVersion.document_id == document.id,
            DocumentVersion.version_number == version_number,
        )
        .first()
    )
    if existing_version is not None:
        raise ConflictError(
            f"Version {version_number} of document '{document.title}' already exists "
            f"(version_id={existing_version.id}). "
            "To re-ingest, delete the existing version first.",
            detail={"version_id": existing_version.id},
        )

    # ── Parse PDF ───────────────────────────────────────────────────────────
    try:
        tree_root = parse_pdf(pdf_path)
    except Exception as exc:
        raise IngestionError(f"PDF parsing failed: {exc}") from exc

    # ── Create DocumentVersion ──────────────────────────────────────────────
    doc_version = DocumentVersion(
        document_id=document.id,
        version_number=version_number,
        file_hash=file_hash,
    )
    db.add(doc_version)
    db.flush()  # Obtain doc_version.id.
    logger.info(
        "Created DocumentVersion id=%d for document_id=%d",
        doc_version.id,
        document.id,
    )

    # ── Persist node tree ───────────────────────────────────────────────────
    nodes_created = _persist_tree(db, tree_root, doc_version.id)
    logger.info(
        "Inserted %d DocumentNode rows for version_id=%d", nodes_created, doc_version.id
    )

    return IngestionResponse(
        document_id=document.id,
        version_id=doc_version.id,
        version_number=version_number,
        nodes_created=nodes_created,
        file_hash=file_hash,
        message=(
            f"Successfully ingested '{pdf_path.name}' as version {version_number} "
            f"with {nodes_created} nodes."
        ),
    )


# ── Private helpers ────────────────────────────────────────────────────────────


def _normalise_document_name(stem: str) -> str:
    """
    Strip version suffixes like '_v2', '_v2.0', '-v2' from a filename stem
    so that ct200_manual and ct200_manual_v2 both map to the same Document.
    """
    import re
    cleaned = re.sub(r"[_\-]v\d+(\.\d+)?$", "", stem, flags=re.IGNORECASE)
    return cleaned.lower()


def _persist_tree(db: Session, root: ParsedNode, version_id: int) -> int:
    """
    Walk the ParsedNode tree in pre-order and bulk-insert DocumentNode rows.

    Uses a uuid_to_orm dict to resolve parent_id FKs before final commit.

    Returns:
        Number of nodes created (excludes root).
    """
    # Map node_uuid → ORM object so children can look up their parent.
    uuid_to_orm: dict[str, DocumentNode] = {}
    nodes_created = 0

    # Use an iterative pre-order traversal to avoid Python recursion limits
    # on deeply nested documents.
    stack = [(root, None)]  # (ParsedNode, parent_orm | None)

    while stack:
        parsed_node, parent_orm = stack.pop(0)  # pop from front = pre-order

        # Skip the synthetic document root node.
        if parsed_node.node_uuid == "root":
            # Push children with parent = None (they will be top-level nodes).
            for child in parsed_node.children:
                stack.insert(0, (child, None))
            continue

        parent_id = parent_orm.id if parent_orm is not None else None

        orm_node = DocumentNode(
            document_version_id=version_id,
            node_uuid=parsed_node.node_uuid,
            parent_id=parent_id,
            heading_level=parsed_node.heading_level,
            numbering=parsed_node.numbering,
            title=parsed_node.title,
            node_type=parsed_node.node_type,
            body_text=parsed_node.body_text,
            content_hash=parsed_node.content_hash,
        )
        db.add(orm_node)
        db.flush()  # Populate orm_node.id before children reference it.

        uuid_to_orm[parsed_node.node_uuid] = orm_node
        nodes_created += 1

        # Push children with this node as parent.
        for child in parsed_node.children:
            stack.insert(0, (child, orm_node))

    return nodes_created
