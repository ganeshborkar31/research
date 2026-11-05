"""add RAG document, version, and ingestion job lifecycle

Revision ID: 20261105_rag_ingestion
Revises: 20261104_remove_tenant
Create Date: 2026-11-05
"""

from alembic import op
import sqlalchemy as sa


revision = "20261105_rag_ingestion"
down_revision = "20261104_remove_tenant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rag_documents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("active_version_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("cleanup_pending", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'activating', 'active', 'failed', 'deleting', 'deleted')",
            name="ck_rag_documents_status",
        ),
    )
    op.create_index("ix_rag_documents_user_id", "rag_documents", ["user_id"])
    op.create_index("ix_rag_documents_knowledge_base_id", "rag_documents", ["knowledge_base_id"])
    op.create_index("ix_rag_documents_status", "rag_documents", ["status"])

    op.create_table(
        "rag_document_versions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("document_id", sa.String(length=36), sa.ForeignKey("rag_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("content_checksum", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("cleanup_pending", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("document_id", "version_number", name="uq_rag_document_versions_number"),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'staged', 'activating', 'indexed', 'superseded', 'failed', 'deleting', 'deleted')",
            name="ck_rag_document_versions_status",
        ),
    )
    op.create_index("ix_rag_document_versions_document_id", "rag_document_versions", ["document_id"])
    op.create_index("ix_rag_document_versions_user_id", "rag_document_versions", ["user_id"])
    op.create_index("ix_rag_document_versions_status", "rag_document_versions", ["status"])

    op.create_table(
        "rag_ingestion_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("document_id", sa.String(length=36), sa.ForeignKey("rag_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_version_id", sa.String(length=36), sa.ForeignKey("rag_document_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("error_stage", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("document_version_id", name="uq_rag_ingestion_jobs_version"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_rag_ingestion_jobs_idempotency"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'retrying', 'succeeded', 'failed', 'cancelled')",
            name="ck_rag_ingestion_jobs_status",
        ),
    )
    op.create_index("ix_rag_ingestion_jobs_document_id", "rag_ingestion_jobs", ["document_id"])
    op.create_index("ix_rag_ingestion_jobs_user_id", "rag_ingestion_jobs", ["user_id"])
    op.create_index("ix_rag_ingestion_jobs_status", "rag_ingestion_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_rag_ingestion_jobs_status", table_name="rag_ingestion_jobs")
    op.drop_index("ix_rag_ingestion_jobs_user_id", table_name="rag_ingestion_jobs")
    op.drop_index("ix_rag_ingestion_jobs_document_id", table_name="rag_ingestion_jobs")
    op.drop_table("rag_ingestion_jobs")
    op.drop_index("ix_rag_document_versions_status", table_name="rag_document_versions")
    op.drop_index("ix_rag_document_versions_user_id", table_name="rag_document_versions")
    op.drop_index("ix_rag_document_versions_document_id", table_name="rag_document_versions")
    op.drop_table("rag_document_versions")
    op.drop_index("ix_rag_documents_status", table_name="rag_documents")
    op.drop_index("ix_rag_documents_knowledge_base_id", table_name="rag_documents")
    op.drop_index("ix_rag_documents_user_id", table_name="rag_documents")
    op.drop_table("rag_documents")
