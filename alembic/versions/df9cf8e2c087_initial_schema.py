"""initial_schema

Revision ID: df9cf8e2c087
Revises:
Create Date: 2026-07-16

Creates all tables for the qa-backend application:
  - documents
  - document_versions
  - document_nodes
  - selections
  - selection_nodes
  - generated_test_case_metadata
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "df9cf8e2c087"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── documents ──────────────────────────────────────────────────────────────
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("file_name", sa.String(512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
    )

    # ── document_versions ──────────────────────────────────────────────────────
    op.create_table(
        "document_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.UniqueConstraint("document_id", "version_number", name="uq_document_version"),
    )

    # ── document_nodes ─────────────────────────────────────────────────────────
    op.create_table(
        "document_nodes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("document_version_id", sa.Integer(), sa.ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("document_nodes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("node_uuid", sa.String(512), nullable=False),
        sa.Column("heading_level", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("numbering", sa.String(64), nullable=False, server_default=""),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("node_type", sa.String(32), nullable=False, server_default="section"),
        sa.Column("body_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Index("ix_document_nodes_version_uuid", "document_version_id", "node_uuid"),
    )

    # ── selections ─────────────────────────────────────────────────────────────
    op.create_table(
        "selections",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("document_version_id", sa.Integer(), sa.ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
    )

    # ── selection_nodes (pivot) ────────────────────────────────────────────────
    op.create_table(
        "selection_nodes",
        sa.Column("selection_id", sa.Integer(), sa.ForeignKey("selections.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("node_id", sa.Integer(), sa.ForeignKey("document_nodes.id", ondelete="CASCADE"), primary_key=True),
    )

    # ── generated_test_case_metadata ───────────────────────────────────────────
    op.create_table(
        "generated_test_case_metadata",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("selection_id", sa.Integer(), sa.ForeignKey("selections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_version_id", sa.Integer(), sa.ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("node_ids_json", sa.Text(), nullable=False),
        sa.Column("content_hashes_json", sa.Text(), nullable=False),
        sa.Column("llm_provider", sa.String(128), nullable=False),
        sa.Column("llm_model", sa.String(256), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("output_file_path", sa.String(1024), nullable=False),
        sa.Column("is_stale", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("stale_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
    )


def downgrade() -> None:
    op.drop_table("generated_test_case_metadata")
    op.drop_table("selection_nodes")
    op.drop_table("selections")
    op.drop_table("document_nodes")
    op.drop_table("document_versions")
    op.drop_table("documents")
