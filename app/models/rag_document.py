"""PostgreSQL source of truth for RAG document ingestion lifecycle."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RagDocument(Base):
    __tablename__ = "rag_documents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'processing', 'activating', 'active', 'failed', 'deleting', 'deleted')",
            name="ck_rag_documents_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    active_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    cleanup_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RagDocumentVersion(Base):
    __tablename__ = "rag_document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_rag_document_versions_number"),
        CheckConstraint(
            "status IN ('queued', 'processing', 'staged', 'activating', 'indexed', 'superseded', 'failed', 'deleting', 'deleted')",
            name="ck_rag_document_versions_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rag_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    content_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cleanup_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RagIngestionJob(Base):
    __tablename__ = "rag_ingestion_jobs"
    __table_args__ = (
        UniqueConstraint("document_version_id", name="uq_rag_ingestion_jobs_version"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_rag_ingestion_jobs_idempotency"),
        CheckConstraint(
            "status IN ('queued', 'running', 'retrying', 'succeeded', 'failed', 'cancelled')",
            name="ck_rag_ingestion_jobs_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rag_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rag_document_versions.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
