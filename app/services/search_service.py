"""
app/services/search_service.py
────────────────────────────────
Full-text search over document nodes.

Implementation:
  - Uses SQLAlchemy ``LIKE`` / ``ILIKE`` for portability across SQLite and
    Postgres.  SQLite does not support ``ILIKE``; we apply ``LOWER()`` on both
    sides for case-insensitive matching.
  - Heading title matches are ranked before body text matches.
  - An optional ``version_id`` filter restricts results to a single version.
  - A short snippet (up to 200 chars) around the first match in body_text is
    included in the response.

Scaling note (in APPROACH.md):
  For large corpora, replace LIKE queries with SQLite FTS5 or Postgres
  ``tsvector`` full-text search.
"""

from __future__ import annotations

import re
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.logging_config import get_logger
from app.models.document import DocumentNode
from app.schemas.search import SearchResponse, SearchResultItem

logger = get_logger(__name__)

_SNIPPET_MAX = 200
_SNIPPET_CONTEXT = 60  # chars before/after match in snippet


def search_nodes(
    db: Session,
    query: str,
    version_id: int | None = None,
    limit: int = 50,
) -> SearchResponse:
    """
    Search for ``query`` in node titles and body text.

    Args:
        db:         Active session.
        query:      Search term (partial matches supported).
        version_id: Restrict to a specific DocumentVersion (optional).
        limit:      Maximum number of results to return (default 50).

    Returns:
        SearchResponse with ranked results.
    """
    if not query or not query.strip():
        return SearchResponse(query=query, total=0, results=[])

    term = query.strip()
    pattern = f"%{term.lower()}%"

    base_q = db.query(DocumentNode)
    if version_id is not None:
        base_q = base_q.filter(DocumentNode.document_version_id == version_id)

    # ── Title matches (higher priority) ─────────────────────────────────────
    title_hits = (
        base_q.filter(func.lower(DocumentNode.title).like(pattern))
        .order_by(DocumentNode.heading_level, DocumentNode.numbering)
        .limit(limit)
        .all()
    )

    # ── Body text matches ─────────────────────────────────────────────────────
    title_ids = {n.id for n in title_hits}
    body_hits = (
        base_q.filter(
            func.lower(DocumentNode.body_text).like(pattern),
            ~DocumentNode.id.in_(title_ids) if title_ids else True,
        )
        .order_by(DocumentNode.heading_level, DocumentNode.numbering)
        .limit(limit)
        .all()
    )

    results: list[SearchResultItem] = []

    for node in title_hits:
        results.append(
            SearchResultItem(
                node_id=node.id,
                document_version_id=node.document_version_id,
                numbering=node.numbering,
                title=node.title,
                node_type=node.node_type,
                snippet=_make_snippet(node.body_text, term),
                match_in="title",
                heading_level=node.heading_level,
            )
        )

    for node in body_hits:
        results.append(
            SearchResultItem(
                node_id=node.id,
                document_version_id=node.document_version_id,
                numbering=node.numbering,
                title=node.title,
                node_type=node.node_type,
                snippet=_make_snippet(node.body_text, term),
                match_in="body",
                heading_level=node.heading_level,
            )
        )

    # Cap at limit.
    results = results[:limit]
    logger.debug("Search '%s': %d results (title=%d, body=%d)", term, len(results), len(title_hits), len(body_hits))

    return SearchResponse(query=query, total=len(results), results=results)


# ── Private helpers ────────────────────────────────────────────────────────────


def _make_snippet(body_text: str, term: str) -> str:
    """
    Return a short excerpt of ``body_text`` centred around the first
    occurrence of ``term`` (case-insensitive).  Returns the first
    ``_SNIPPET_MAX`` chars if the term is not found.
    """
    if not body_text:
        return ""
    lower_body = body_text.lower()
    lower_term = term.lower()
    pos = lower_body.find(lower_term)
    if pos == -1:
        return body_text[:_SNIPPET_MAX]

    start = max(0, pos - _SNIPPET_CONTEXT)
    end = min(len(body_text), pos + len(term) + _SNIPPET_CONTEXT)
    snippet = body_text[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(body_text):
        snippet = snippet + "…"
    return snippet[:_SNIPPET_MAX]
