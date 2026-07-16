"""
app/api/v1/search.py
─────────────────────
Search endpoint.

GET /api/v1/search?q=<term>&version_id=<id>&limit=<n>
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.schemas.search import SearchResponse
from app.services.search_service import search_nodes

router = APIRouter(prefix="/search", tags=["Search"])


@router.get(
    "",
    response_model=SearchResponse,
    summary="Search nodes by heading and body text",
)
def search(
    q: str = Query(..., min_length=1, description="Search term"),
    version_id: int | None = Query(None, description="Restrict to a specific version"),
    limit: int = Query(50, ge=1, le=200, description="Maximum number of results"),
    db: Session = Depends(get_db),
) -> SearchResponse:
    """
    Search for the given term across all node titles and body text.

    - Title matches are ranked above body text matches.
    - Partial matches are supported (e.g. ``safe`` matches ``Safety``,
      ``Safeguards``, etc.).
    - Search is case-insensitive.
    - Optionally restrict to a single document version via ``version_id``.
    """
    return search_nodes(db=db, query=q, version_id=version_id, limit=limit)
