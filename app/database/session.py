"""
app/database/session.py
────────────────────────
Database session factory and FastAPI dependency.

``get_db()`` is injected into route functions via FastAPI's ``Depends()``
mechanism.  It yields a session and guarantees rollback on error and close
on exit, so callers never need to manage sessions manually.
"""

from collections.abc import Generator
from sqlalchemy.orm import Session, sessionmaker
from app.database.base import engine


# Session factory — reuse across requests, but each request gets its own
# session instance from the pool.
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,  # Avoids lazy-load errors after commit.
)


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency that yields a database session.

    Usage::

        @router.get("/example")
        def example(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
