"""
app/api/v1/documents.py
────────────────────────
Routes for document and version management.

POST /api/v1/documents/ingest  – Upload a PDF and ingest it as a new version.
GET  /api/v1/documents         – List all documents.
GET  /api/v1/documents/{id}    – Get a single document with version list.
GET  /api/v1/versions          – List all document versions.
GET  /api/v1/versions/{id}     – Get a specific version.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.database.session import get_db
from app.models.document import Document, DocumentNode, DocumentVersion
from app.schemas.document import (
    DocumentListResponse,
    DocumentResponse,
    DocumentVersionListResponse,
    DocumentVersionResponse,
    IngestionResponse,
)
from app.services.ingestion_service import ingest_pdf
from app.services.staleness_service import run_staleness_check

router = APIRouter(prefix="/documents", tags=["Documents"])
versions_router = APIRouter(prefix="/versions", tags=["Versions"])


# ── Document routes ────────────────────────────────────────────────────────────


@router.post(
    "/ingest",
    response_model=IngestionResponse,
    status_code=201,
    summary="Ingest a PDF as a new document version",
)
def ingest_document(
    file: UploadFile = File(..., description="PDF file to ingest"),
    version_number: int = Form(..., ge=1, description="Version number (e.g. 1 or 2)"),
    document_title: str | None = Form(None, description="Override document title"),
    db: Session = Depends(get_db),
) -> IngestionResponse:
    """
    Upload a PDF file and ingest it as a new document version.

    The file is saved to a temp directory, parsed, and then persisted.
    Existing versions are never overwritten — attempting to re-ingest the
    same version number raises 409 Conflict.

    After ingestion, a staleness check is automatically triggered for all
    existing generations associated with this document.
    """
    # Save upload to a temp file so the parser can read it by path.
    suffix = Path(file.filename or "upload.pdf").suffix or ".pdf"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    try:
        result = ingest_pdf(
            db=db,
            pdf_path=tmp_path,
            version_number=version_number,
            document_title=document_title,
        )
        # Trigger staleness check after ingestion.
        if version_number > 1:
            run_staleness_check(db, result.document_id)
    finally:
        tmp_path.unlink(missing_ok=True)

    return result


@router.get(
    "",
    response_model=DocumentListResponse,
    summary="List all documents",
)
def list_documents(db: Session = Depends(get_db)) -> DocumentListResponse:
    """Return all ingested documents with version counts."""
    documents = db.query(Document).order_by(Document.created_at.desc()).all()
    items = []
    for doc in documents:
        version_count = (
            db.query(DocumentVersion)
            .filter(DocumentVersion.document_id == doc.id)
            .count()
        )
        items.append(
            DocumentResponse(
                id=doc.id,
                title=doc.title,
                file_name=doc.file_name,
                created_at=doc.created_at,
                version_count=version_count,
            )
        )
    return DocumentListResponse(total=len(items), items=items)


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Get a document by ID",
)
def get_document(document_id: int, db: Session = Depends(get_db)) -> DocumentResponse:
    """Return a single document by its ID."""
    doc = db.get(Document, document_id)
    if doc is None:
        raise NotFoundError(f"Document {document_id} not found.")
    version_count = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document_id)
        .count()
    )
    return DocumentResponse(
        id=doc.id,
        title=doc.title,
        file_name=doc.file_name,
        created_at=doc.created_at,
        version_count=version_count,
    )


# ── Version routes ─────────────────────────────────────────────────────────────


@versions_router.get(
    "",
    response_model=DocumentVersionListResponse,
    summary="List versions for a document",
)
def list_versions(
    document_id: int = Query(..., description="Parent document ID"),
    db: Session = Depends(get_db),
) -> DocumentVersionListResponse:
    """List all versions of a given document."""
    doc = db.get(Document, document_id)
    if doc is None:
        raise NotFoundError(f"Document {document_id} not found.")

    versions = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number)
        .all()
    )

    version_responses = []
    for v in versions:
        node_count = (
            db.query(DocumentNode)
            .filter(DocumentNode.document_version_id == v.id)
            .count()
        )
        version_responses.append(
            DocumentVersionResponse(
                id=v.id,
                document_id=v.document_id,
                version_number=v.version_number,
                file_hash=v.file_hash,
                ingested_at=v.ingested_at,
                node_count=node_count,
            )
        )

    return DocumentVersionListResponse(document_id=document_id, versions=version_responses)


@versions_router.get(
    "/{version_id}",
    response_model=DocumentVersionResponse,
    summary="Get a specific version",
)
def get_version(version_id: int, db: Session = Depends(get_db)) -> DocumentVersionResponse:
    """Return a single document version by its ID."""
    version = db.get(DocumentVersion, version_id)
    if version is None:
        raise NotFoundError(f"DocumentVersion {version_id} not found.")
    node_count = (
        db.query(DocumentNode)
        .filter(DocumentNode.document_version_id == version_id)
        .count()
    )
    return DocumentVersionResponse(
        id=version.id,
        document_id=version.document_id,
        version_number=version.version_number,
        file_hash=version.file_hash,
        ingested_at=version.ingested_at,
        node_count=node_count,
    )
