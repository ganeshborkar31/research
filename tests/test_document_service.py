from datetime import datetime, timezone

import pytest

from app.schemas.user_memory import UserMemoryHit
from app.services.document_service import DocumentService


class FakeMemoryStore:
    def __init__(self) -> None:
        self.documents = []
        self.search_calls = []

    async def store_user_document(self, payload):
        self.documents.append(payload)

    async def search_user_documents(
        self,
        *,
        tenant_id: str,
        user_id: str,
        query: str,
        chat_id: str | None = None,
        include_global: bool = True,
        limit: int = 5,
    ):
        self.search_calls.append(
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "query": query,
                "chat_id": chat_id,
                "include_global": include_global,
                "limit": limit,
            }
        )
        return [
            UserMemoryHit(
                point_id="pt-1",
                score=0.88,
                content="Company security policy",
                source="uploaded_document",
                created_at=datetime.now(timezone.utc),
                metadata={"filename": "policy.txt"},
            )
        ]


@pytest.mark.asyncio
async def test_ingest_text_chunks_and_stores_chat_scope():
    fake_store = FakeMemoryStore()
    service = DocumentService(memory_store=fake_store, chunk_size=100, chunk_overlap=20)

    result = await service.ingest_text(
        tenant_id="tenant-1",
        user_id="user-1",
        chat_id="chat-1",
        content=("This is a long policy text with enterprise controls. " * 10).strip(),
        source="uploaded_document",
        metadata={"filename": "policy.txt"},
        document_id="doc-123",
    )

    assert result.document_id == "doc-123"
    assert result.chunk_count >= 2
    assert len(fake_store.documents) == result.chunk_count

    first_payload = fake_store.documents[0]
    assert first_payload.chat_id == "chat-1"
    assert first_payload.source == "uploaded_document"
    assert first_payload.metadata["document_id"] == "doc-123"
    assert first_payload.metadata["chunk_count"] == result.chunk_count


@pytest.mark.asyncio
async def test_ingest_file_rejects_pdf_without_parser():
    fake_store = FakeMemoryStore()
    service = DocumentService(memory_store=fake_store)

    with pytest.raises(ValueError, match="PDF parsing is not enabled"):
        await service.ingest_file(
            tenant_id="tenant-1",
            user_id="user-1",
            chat_id="chat-1",
            file_bytes=b"%PDF-1.7 binary",
            filename="resume.pdf",
            content_type="application/pdf",
            source="upload",
            metadata={},
        )


@pytest.mark.asyncio
async def test_search_documents_passes_chat_scope_and_limit():
    fake_store = FakeMemoryStore()
    service = DocumentService(memory_store=fake_store)

    hits = await service.search_documents(
        tenant_id="tenant-1",
        user_id="user-1",
        chat_id="chat-9",
        query="security policy",
        limit=3,
        include_global=False,
    )

    assert len(hits) == 1
    assert fake_store.search_calls[0]["chat_id"] == "chat-9"
    assert fake_store.search_calls[0]["include_global"] is False
    assert fake_store.search_calls[0]["limit"] == 3
