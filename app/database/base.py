"""
app/database/base.py
─────────────────────
SQLAlchemy engine and declarative base.

All ORM models import ``Base`` from this module so that Alembic's
autogenerate can discover every table in one place.  The engine is created
once and shared for the application lifetime.

Design notes:
  - ``check_same_thread=False`` is required for SQLite when using FastAPI's
    async request handling (threads differ between request setup and teardown).
  - ``pool_pre_ping=True`` keeps connections alive across idle periods.
  - For a production Postgres migration, only DATABASE_URL needs to change.
"""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


# ── Engine ────────────────────────────────────────────────────────────────────

_connect_args: dict = {}
if settings.database_url.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    pool_pre_ping=True,
    echo=False,  # Set to True for SQL query logging during debugging.
)


# Enable WAL mode for SQLite so readers don't block writers.
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):  # type: ignore[no-untyped-def]
    if settings.database_url.startswith("sqlite"):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
