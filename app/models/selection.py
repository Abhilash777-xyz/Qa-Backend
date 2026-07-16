"""
app/models/selection.py
────────────────────────
ORM models for named, version-pinned node selections.

A Selection captures a user's choice of document nodes at a specific version.
Even if Version 2 is later imported, existing selections continue to resolve
against the version they were created with.

Relationships:
    Selection  *──*  DocumentNode   (via SelectionNode pivot)
    Selection  *──1  DocumentVersion (pinned)
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SelectionNode(Base):
    """
    Pivot table linking selections to specific document nodes.

    Both ``selection_id`` and ``node_id`` form a composite primary key so
    the same node cannot be added to the same selection twice.
    """

    __tablename__ = "selection_nodes"

    selection_id: Mapped[int] = mapped_column(
        ForeignKey("selections.id", ondelete="CASCADE"), primary_key=True
    )
    node_id: Mapped[int] = mapped_column(
        ForeignKey("document_nodes.id", ondelete="CASCADE"), primary_key=True
    )


class Selection(Base):
    """
    A named, version-pinned collection of document nodes.

    ``document_version_id`` is set at creation and never changed.  This is
    the version-pinning mechanism: all node lookups for this selection use
    only nodes from that version.
    """

    __tablename__ = "selections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    document_version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )

    # Pinned version (read-only after creation).
    pinned_version: Mapped["DocumentVersion"] = relationship(  # noqa: F821
        "DocumentVersion", lazy="joined"
    )

    # Nodes in this selection (resolved from the pinned version).
    nodes: Mapped[list["DocumentNode"]] = relationship(  # noqa: F821
        "DocumentNode",
        secondary="selection_nodes",
        lazy="select",
    )

    def __repr__(self) -> str:
        return f"<Selection id={self.id} name={self.name!r} version={self.document_version_id}>"
