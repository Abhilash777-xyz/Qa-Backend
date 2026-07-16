"""
app/api/router.py
──────────────────
Central router that aggregates all API v1 sub-routers.

Import this in app/main.py and register with ``app.include_router(api_router)``.
All routes are mounted under the ``/api/v1`` prefix defined here.
"""

from fastapi import APIRouter

from app.api.v1 import changes, documents, generation, nodes, search, selections

api_router = APIRouter(prefix="/api/v1")

# ── Register sub-routers ───────────────────────────────────────────────────────
api_router.include_router(documents.router)
api_router.include_router(documents.versions_router)
api_router.include_router(nodes.router)
api_router.include_router(search.router)
api_router.include_router(changes.router)
api_router.include_router(selections.router)
api_router.include_router(generation.router)
