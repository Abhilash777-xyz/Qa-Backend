"""
app/core/logging_config.py
──────────────────────────
Configures the root Python logger.  In development, log records are emitted
as human-readable coloured text.  The format is kept consistent so it can be
adapted to structured JSON (e.g. via python-json-logger) for production
without changing any call-sites.

Call ``setup_logging()`` exactly once at application startup (in main.py).
"""

import logging
import sys
from app.core.config import settings


def setup_logging() -> None:
    """Configure root logger based on settings.log_level."""

    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)
    handler.setFormatter(logging.Formatter(fmt=fmt, datefmt=datefmt))

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove any default handlers first to avoid duplicate lines.
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    # Silence noisy third-party loggers.
    for noisy in ("uvicorn.access", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Logging initialised at level=%s env=%s",
        settings.log_level,
        settings.app_env,
    )


def get_logger(name: str) -> logging.Logger:
    """
    Convenience wrapper: call ``get_logger(__name__)`` at the top of each
    module to obtain a named logger that inherits root configuration.
    """
    return logging.getLogger(name)
