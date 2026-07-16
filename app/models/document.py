"""
app/models/document.py
───────────────────────
ORM models for the core document hierarchy.

Entity relationships:
    Document  1──*  DocumentVersion  1──*  DocumentNode
                                               │
                                     parent_id └──(self-ref FK)

Design decisions:
  - ``DocumentNode.node_uuid`` is a stable, human-readable identifier derived
    from the node's numbering/title so it persists across re-ingestions.
  - ``content_hash`` is SHA-256 of (title + numbering + body_text) — it is
    the single source of truth for change detection.
  - ``heading_level`` 0 = document root; 1–6 mirror HTML heading semantics.
  - Tables and lists are stored as JSON strings in ``body_text`` with a
    ``node_type`` discriminator so the parser output survives SQLite's lack
    of a native JSON column type (TEXT works fine; we parse on read).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Document(Base):
    """
    Top-level document record.  One document may have many versions.
    E.g.  ``title="CT200 Manual"`` with versions 1 and 2.
    """

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )

    versions: Mapped[list[DocumentVersion]] = relationship(
        "DocumentVersion", back_populates="document", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Document id={self.id} title={self.title!r}>"


class DocumentVersion(Base):
    """
    An immutable snapshot of a document at a point in time.
    Once created, rows in this table are never updated or deleted.
    """

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_doc_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )

    document: Mapped[Document] = relationship("Document", back_populates="versions")
    nodes: Mapped[list[DocumentNode]] = relationship(
        "DocumentNode",
        back_populates="version",
        cascade="all, delete-orphan",
        foreign_keys="DocumentNode.document_version_id",
    )

    def __repr__(self) -> str:
        return f"<DocumentVersion id={self.id} v={self.version_number}>"


class DocumentNode(Base):
    """
    A single node in the document tree.

    ``node_uuid`` is a deterministic identifier built from the numbering (e.g.
    "2.1.3") or a slug of the title so that the *same* conceptual section can
    be matched across version 1 and version 2 rows during diffing.

    ``node_type`` discriminates how ``body_text`` should be interpreted:
        "section"  → plain text
        "table"    → JSON-encoded list[list[str]]
        "list"     → JSON-encoded list[str]
    """

    __tablename__ = "document_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    document_version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Stable cross-version identity key (e.g. "2.1.3" or slugified title).
    node_uuid: Mapped[str] = mapped_column(String(256), nullable=False, index=True)

    # Hierarchy.
    parent_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("document_nodes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    heading_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    numbering: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    # Content.
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    node_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="section"
    )  # section | table | list
    body_text: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Change-detection fingerprint.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )

    # Relationships.
    version: Mapped[DocumentVersion] = relationship(
        "DocumentVersion", back_populates="nodes"
    )
    children: Mapped[list[DocumentNode]] = relationship(
        "DocumentNode",
        primaryjoin="DocumentNode.parent_id == DocumentNode.id",
        foreign_keys="DocumentNode.parent_id",
        lazy="select",
    )

    def __repr__(self) -> str:
        return (
            f"<DocumentNode id={self.id} numbering={self.numbering!r} "
            f"title={self.title!r} level={self.heading_level}>"
        )
