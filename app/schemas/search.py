"""
app/schemas/search.py
──────────────────────
Pydantic models for the search API.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SearchResultItem(BaseModel):
    """A single search hit."""

    node_id: int
    document_version_id: int
    numbering: str
    title: str
    node_type: str
    snippet: str = Field("", description="Short excerpt of body_text around the match")
    match_in: str = Field("", description="'title' or 'body' — where the match was found")
    heading_level: int


class SearchResponse(BaseModel):
    """Response for GET /api/v1/search."""

    query: str
    total: int
    results: list[SearchResultItem]
