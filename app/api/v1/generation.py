"""
app/api/v1/generation.py
─────────────────────────
LLM test-case generation and retrieval endpoints.

POST /api/v1/generate                       – Generate test cases for a selection.
GET  /api/v1/testcases/{generation_id}      – Retrieve a generation by ID.
GET  /api/v1/testcases/selection/{sel_id}   – All generations for a selection.
GET  /api/v1/testcases/node/{node_id}       – Generations that include a node.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.database.session import get_db
from app.models.document import DocumentNode
from app.models.generation import GeneratedTestCaseMetadata
from app.schemas.generation import (
    GenerateRequest,
    GenerationResponse,
    TestCaseListResponse,
)
from app.services.llm_service import generate_test_cases, load_generation
from app.services.staleness_service import check_generation_staleness

router = APIRouter(tags=["Test Case Generation"])


@router.post(
    "/generate",
    response_model=GenerationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate QA test cases for a selection",
)
def generate(
    payload: GenerateRequest,
    db: Session = Depends(get_db),
) -> GenerationResponse:
    """
    Generate 3–5 structured QA test cases for the nodes in the given
    selection using the configured LLM provider (OpenRouter).

    The generation is stored on disk as JSON + a metadata row in SQLite.
    Re-generating the same selection creates a new generation row.

    Requires ``OPENROUTER_API_KEY`` to be set in the environment.
    Returns 503 if the API key is not configured.
    """
    return generate_test_cases(db=db, selection_id=payload.selection_id)


@router.get(
    "/testcases/{generation_id}",
    response_model=GenerationResponse,
    summary="Retrieve a generation by ID",
)
def get_generation(
    generation_id: int,
    db: Session = Depends(get_db),
) -> GenerationResponse:
    """
    Return a previously generated set of test cases by generation ID.

    Runs an on-demand staleness check before responding, so the ``is_stale``
    flag is always up-to-date.
    """
    check_generation_staleness(db=db, generation_id=generation_id)
    return load_generation(db=db, generation_id=generation_id)


@router.get(
    "/testcases/selection/{selection_id}",
    response_model=list[GenerationResponse],
    summary="All generations for a selection",
)
def get_generations_for_selection(
    selection_id: int,
    db: Session = Depends(get_db),
) -> list[GenerationResponse]:
    """
    Return all generations (ordered newest first) for a given selection.

    Each response includes ``is_stale`` status.
    """
    metas = (
        db.query(GeneratedTestCaseMetadata)
        .filter(GeneratedTestCaseMetadata.selection_id == selection_id)
        .order_by(GeneratedTestCaseMetadata.generated_at.desc())
        .all()
    )
    results = []
    for meta in metas:
        try:
            resp = load_generation(db=db, generation_id=meta.id)
            results.append(resp)
        except NotFoundError:
            continue  # Output file missing — skip silently.
    return results


@router.get(
    "/testcases/node/{node_id}",
    response_model=list[GenerationResponse],
    summary="All generations that include a specific node",
)
def get_generations_for_node(
    node_id: int,
    db: Session = Depends(get_db),
) -> list[GenerationResponse]:
    """
    Return all test case generations that included the given node.

    Searches the stored ``node_ids_json`` field.  Useful for tracing which
    test cases cover a specific document requirement.
    """
    node = db.get(DocumentNode, node_id)
    if node is None:
        raise NotFoundError(f"Node {node_id} not found.")

    # Load all metadata and filter in Python (acceptable at this scale).
    all_metas = db.query(GeneratedTestCaseMetadata).all()
    matching: list[GeneratedTestCaseMetadata] = []
    for meta in all_metas:
        try:
            ids: list[int] = json.loads(meta.node_ids_json)
            if node_id in ids:
                matching.append(meta)
        except (ValueError, TypeError):
            continue

    results = []
    for meta in sorted(matching, key=lambda m: m.generated_at, reverse=True):
        try:
            results.append(load_generation(db=db, generation_id=meta.id))
        except NotFoundError:
            continue
    return results
