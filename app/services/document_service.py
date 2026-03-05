from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.schemas.user_memory import UserDocumentIn, UserMemoryHit
from app.services.user_vector_store import UserVectorStore


_SUPPORTED_TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".json",
    ".log",
    ".rst",
    ".yaml",
    ".yml",
    ".xml",
    ".html",
    ".htm",
}


@dataclass
class DocumentIngestResult:
    document_id: str
    source: str
    chunk_count: int
    total_characters: int
    created_at: datetime


class DocumentService:
    def __init__(
        self,
        *,
        memory_store: UserVectorStore | None = None,
        chunk_size: int = 1200,
        chunk_overlap: int = 200,
    ) -> None:
        if chunk_size < 100:
            raise ValueError("chunk_size must be >= 100 characters.")
        if chunk_overlap < 0:
            raise ValueError("chunk_overlap cannot be negative.")
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size.")

        self._vector_store = memory_store or UserVectorStore()
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    async def ingest_text(
        self,
        *,
        tenant_id: str,
        user_id: str,
        chat_id: str,
        content: str,
        source: str = "user_document",
        metadata: dict[str, Any] | None = None,
        document_id: str | None = None,
    ) -> DocumentIngestResult:
        cleaned = self._normalize_content(content)
        if not cleaned:
            raise ValueError("Document content cannot be empty.")

        base_document_id = (document_id or str(uuid4())).strip()
        if not base_document_id:
            raise ValueError("document_id cannot be blank.")

        source_value = (source or "user_document").strip() or "user_document"
        base_metadata = dict(metadata or {})
        chunks = self._chunk_text(cleaned)
        chunk_count = len(chunks)
        created_at = datetime.now(timezone.utc)

        for index, chunk in enumerate(chunks, start=1):
            chunk_document_id = base_document_id if chunk_count == 1 else f"{base_document_id}:chunk:{index}"
            chunk_metadata = {
                **base_metadata,
                "chunk_index": index,
                "chunk_count": chunk_count,
                "document_id": base_document_id,
            }
            await self._vector_store.store_user_document(
                UserDocumentIn(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    chat_id=chat_id,
                    document_id=chunk_document_id,
                    content=chunk,
                    source=source_value,
                    metadata=chunk_metadata,
                )
            )

        return DocumentIngestResult(
            document_id=base_document_id,
            source=source_value,
            chunk_count=chunk_count,
            total_characters=len(cleaned),
            created_at=created_at,
        )

    async def ingest_file(
        self,
        *,
        tenant_id: str,
        user_id: str,
        chat_id: str,
        file_bytes: bytes,
        filename: str | None,
        content_type: str | None,
        source: str | None = None,
        metadata: dict[str, Any] | None = None,
        document_id: str | None = None,
    ) -> DocumentIngestResult:
        parsed_text = self._extract_text(file_bytes=file_bytes, filename=filename, content_type=content_type)
        base_metadata = dict(metadata or {})
        if filename:
            base_metadata.setdefault("filename", filename)
        if content_type:
            base_metadata.setdefault("content_type", content_type)

        inferred_source = (source or "uploaded_document").strip() or "uploaded_document"
        return await self.ingest_text(
            tenant_id=tenant_id,
            user_id=user_id,
            chat_id=chat_id,
            content=parsed_text,
            source=inferred_source,
            metadata=base_metadata,
            document_id=document_id,
        )

    async def search_documents(
        self,
        *,
        tenant_id: str,
        user_id: str,
        chat_id: str,
        query: str,
        limit: int = 5,
        include_global: bool = True,
    ) -> list[UserMemoryHit]:
        text_query = query.strip()
        if not text_query:
            raise ValueError("Query cannot be empty.")

        return await self._vector_store.search_user_documents(
            tenant_id=tenant_id,
            user_id=user_id,
            chat_id=chat_id,
            query=text_query,
            include_global=include_global,
            limit=limit,
        )

    def _extract_text(self, *, file_bytes: bytes, filename: str | None, content_type: str | None) -> str:
        if not file_bytes:
            raise ValueError("Uploaded file is empty.")

        extension = Path((filename or "").strip()).suffix.lower()
        content_type_value = (content_type or "").lower()

        if content_type_value.startswith("text/") or extension in _SUPPORTED_TEXT_EXTENSIONS:
            return self._decode_text(file_bytes)

        if extension == ".pdf":
            raise ValueError(
                "PDF parsing is not enabled in this build. Upload text/markdown/csv/json, "
                "or send extracted PDF text via the text endpoint."
            )

        if extension in {".doc", ".docx"}:
            raise ValueError(
                "DOC/DOCX parsing is not enabled in this build. Export as text or markdown and upload again."
            )

        raise ValueError("Unsupported document type. Upload text-like files such as .txt, .md, .csv, or .json.")

    @staticmethod
    def _decode_text(file_bytes: bytes) -> str:
        try:
            return file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return file_bytes.decode("utf-8", errors="ignore")

    @staticmethod
    def _normalize_content(content: str) -> str:
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized.strip()

    def _chunk_text(self, content: str) -> list[str]:
        if len(content) <= self._chunk_size:
            return [content]

        chunks: list[str] = []
        start = 0
        text_length = len(content)

        while start < text_length:
            raw_end = min(start + self._chunk_size, text_length)
            end = raw_end

            if raw_end < text_length:
                newline_split = content.rfind("\n", start, raw_end)
                space_split = content.rfind(" ", start, raw_end)
                natural_split = max(newline_split, space_split)
                if natural_split > start + (self._chunk_size // 2):
                    end = natural_split

            if end <= start:
                end = raw_end

            chunk = content[start:end].strip()
            if chunk:
                chunks.append(chunk)

            if end >= text_length:
                break
            start = max(end - self._chunk_overlap, start + 1)

        return chunks


_document_service: DocumentService | None = None


def get_document_service() -> DocumentService:
    global _document_service
    if _document_service is None:
        _document_service = DocumentService()
    return _document_service
