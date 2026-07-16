"""
app/core/exceptions.py
──────────────────────
Application-specific exception hierarchy.

Each exception carries an HTTP status code and a machine-readable ``code``
string so that the global exception handlers in main.py can return consistent
JSON error responses without scattering HTTPException raises throughout
business logic.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for all application exceptions."""

    http_status: int = 500
    code: str = "INTERNAL_ERROR"

    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload


# ── 400-range ─────────────────────────────────────────────────────────────────


class ValidationError(AppError):
    """Raised when incoming data fails business-rule validation."""

    http_status = 422
    code = "VALIDATION_ERROR"


class ConflictError(AppError):
    """Raised when an operation would create a duplicate resource."""

    http_status = 409
    code = "CONFLICT"


# ── 404 ───────────────────────────────────────────────────────────────────────


class NotFoundError(AppError):
    """Raised when a requested resource does not exist."""

    http_status = 404
    code = "NOT_FOUND"


# ── Parser ────────────────────────────────────────────────────────────────────


class ParserError(AppError):
    """Raised when the PDF parser encounters an unrecoverable problem."""

    http_status = 422
    code = "PARSER_ERROR"


# ── LLM ───────────────────────────────────────────────────────────────────────


class LLMError(AppError):
    """Raised when LLM generation fails after all retries."""

    http_status = 502
    code = "LLM_ERROR"


class LLMResponseParseError(LLMError):
    """
    Raised when the LLM returned a response but it could not be parsed into
    the expected structured format even after a retry.
    """

    code = "LLM_PARSE_ERROR"


class LLMNotConfiguredError(LLMError):
    """Raised when no valid API key is configured."""

    http_status = 503
    code = "LLM_NOT_CONFIGURED"


# ── Ingestion ─────────────────────────────────────────────────────────────────


class IngestionError(AppError):
    """Raised when document ingestion fails."""

    http_status = 422
    code = "INGESTION_ERROR"
