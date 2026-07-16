"""
app/main.py
────────────
FastAPI application factory.

Responsibilities:
  - Create and configure the FastAPI app instance.
  - Register the global exception handlers (AppError subclasses → JSON).
  - Mount the API router.
  - Run database table creation on startup.
  - Add CORS middleware.

Run with:
    uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging_config import setup_logging
from app.database.base import Base, engine

# Initialise logging as early as possible.
setup_logging()

logger = logging.getLogger(__name__)


# ── Lifespan ───────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan handler.

    Startup:
      - Create all SQLAlchemy tables (idempotent — uses CREATE TABLE IF NOT EXISTS).
      - Log configuration summary.

    Shutdown:
      - No explicit cleanup needed for SQLite; the connection pool closes
        when the process exits.
    """
    logger.info("Starting qa-backend (env=%s)", settings.app_env)
    logger.info("Database: %s", settings.database_url)
    logger.info("LLM configured: %s (model=%s)", settings.llm_configured, settings.openrouter_model)

    # Create tables — safe to run on every startup.
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables ready.")

    yield

    logger.info("Shutting down qa-backend.")


# ── Application factory ────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    app = FastAPI(
        title="QA Backend",
        description=(
            "Production-grade document ingestion, versioning, change detection, "
            "and LLM-powered QA test-case generation API.\n\n"
            "Built with FastAPI · SQLAlchemy · pdfplumber · OpenRouter."
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Restrict in production.
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Global exception handlers ─────────────────────────────────────────────

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "AppError [%s] on %s %s: %s",
            exc.code,
            request.method,
            request.url.path,
            exc.message,
        )
        return JSONResponse(
            status_code=exc.http_status,
            content=exc.to_dict(),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "Unhandled exception on %s %s", request.method, request.url.path
        )
        return JSONResponse(
            status_code=500,
            content={
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred. Check server logs.",
            },
        )

    # ── Routes ────────────────────────────────────────────────────────────────
    app.include_router(api_router)

    # ── Health check ──────────────────────────────────────────────────────────
    @app.get("/health", tags=["Health"], summary="Health check")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": "1.0.0",
            "env": settings.app_env,
            "llm_configured": settings.llm_configured,
        }

    return app


app = create_app()
