"""
app/models/generation.py
─────────────────────────
ORM model for LLM-generated test case metadata.

The actual test case content is stored as a JSON file on disk
(``output_file_path``).  This table stores only the metadata needed for
querying, staleness detection, and auditing — keeping the SQL schema stable
regardless of how the LLM output format evolves.

Staleness flag lifecycle:
    1. Set ``is_stale = False`` at creation.
    2. When a new document version is ingested, ``StalenessService`` re-reads
       the stored ``content_hashes_json`` and compares them against the latest
       version's node hashes.
    3. If any hash has changed, ``is_stale`` is flipped to ``True`` and
       ``stale_reason`` explains which nodes changed.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GeneratedTestCaseMetadata(Base):
    """
    Metadata row for one LLM generation run.

    ``node_ids_json``      – JSON array of DocumentNode.id values used.
    ``content_hashes_json``– JSON array of content_hash strings at generation
                             time; used later to detect staleness.
    ``output_file_path``   – Absolute path to the JSON file on disk that
                             holds the structured test-case array.
    """

    __tablename__ = "generated_test_case_metadata"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    selection_id: Mapped[int] = mapped_column(
        ForeignKey("selections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=False
    )

    # Serialised node references captured at generation time.
    node_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    content_hashes_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    # LLM provenance.
    llm_provider: Mapped[str] = mapped_column(String(128), nullable=False, default="openrouter")
    llm_model: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1")

    # Output artifact.
    output_file_path: Mapped[str] = mapped_column(String(512), nullable=False, default="")

    # Staleness tracking.
    is_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stale_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")

    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )

    # Relationships.
    selection: Mapped["Selection"] = relationship("Selection", lazy="joined")  # noqa: F821
    document_version: Mapped["DocumentVersion"] = relationship(  # noqa: F821
        "DocumentVersion", lazy="joined"
    )

    def __repr__(self) -> str:
        return (
            f"<GeneratedTestCaseMetadata id={self.id} "
            f"selection={self.selection_id} stale={self.is_stale}>"
        )
