from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from math import sqrt
from random import Random
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels

from app.core.config import get_settings
from app.schemas.user_memory import UserDocumentIn, UserHistoryEventIn, UserMemoryHit
from app.models.user import User


class UserVectorStore:
    """Stores user documents and conversation history in Qdrant."""

    def __init__(
        self,
        *,
        client: AsyncQdrantClient | Any | None = None,
        docs_collection: str = "user_documents_v1",
        history_collection: str = "user_history_v1",
        vector_size: int = 256,
    ) -> None:
        settings = get_settings()
        self._client = client or AsyncQdrantClient(url=settings.qdrant_url)
        self._owns_client = client is None
        self._docs_collection = docs_collection
        self._history_collection = history_collection
        self._vector_size = vector_size
        self._collections_ready = False

    async def close(self) -> None:
        if self._owns_client:
            await self._client.close()

    async def store_user_document(self, payload: UserDocumentIn) -> None:
        await self._ensure_collections()
        vector = self._embed_text(payload.content)
        scoped_chat_id = payload.chat_id or "__global__"
        point = qmodels.PointStruct(
            id=f"{payload.tenant_id}:{payload.user_id}:{payload.document_id}",
            vector=vector,
            payload={
                "tenant_id": payload.tenant_id,
                "user_id": payload.user_id,
                "chat_id": scoped_chat_id,
                "record_type": "document",
                "source": payload.source,
                "content": payload.content,
                "document_id": payload.document_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "metadata": payload.metadata,
            },
        )
        await self._client.upsert(collection_name=self._docs_collection, points=[point])

    async def store_history_event(self, payload: UserHistoryEventIn) -> None:
        await self._ensure_collections()
        vector = self._embed_text(payload.message)
        point = qmodels.PointStruct(
            id=sha256(
                f"{payload.tenant_id}:{payload.user_id}:{payload.session_id}:{payload.role}:{payload.message}".encode(
                    "utf-8"
                )
            ).hexdigest(),
            vector=vector,
            payload={
                "tenant_id": payload.tenant_id,
                "user_id": payload.user_id,
                "session_id": payload.session_id,
                "record_type": "history",
                "role": payload.role,
                "source": "chat_history",
                "content": payload.message,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "metadata": payload.metadata,
            },
        )
        await self._client.upsert(collection_name=self._history_collection, points=[point])

    async def index_user_profile_snapshot(self, user: User) -> None:
        profile_text = (
            f"User profile for {user.email}. "
            f"Username: {user.username}. "
            f"Name: {(user.first_name or '').strip()} {(user.last_name or '').strip()}. "
            f"Department: {user.department or 'N/A'}. "
            f"Title: {user.title or 'N/A'}. "
            f"Timezone: {user.timezone or 'N/A'}. "
            f"Status: {user.status}. "
            f"Personal data: {user.personal_data}. "
            f"Preferences: {user.preferences}."
        )
        await self.store_user_document(
            UserDocumentIn(
                tenant_id=user.tenant_id,
                user_id=user.id,
                document_id="profile_snapshot",
                content=profile_text,
                source="profile_snapshot",
                metadata={
                    "email": user.email,
                    "is_verified": user.is_verified,
                    "compliance_flags": user.compliance_flags,
                },
            )
        )

    async def search_user_documents(
        self,
        *,
        tenant_id: str,
        user_id: str,
        query: str,
        chat_id: str | None = None,
        include_global: bool = True,
        limit: int = 5,
    ) -> list[UserMemoryHit]:
        return await self._search(
            collection_name=self._docs_collection,
            tenant_id=tenant_id,
            user_id=user_id,
            query=query,
            chat_id=chat_id,
            include_global=include_global,
            limit=limit,
        )

    async def search_user_history(
        self,
        *,
        tenant_id: str,
        user_id: str,
        query: str,
        chat_id: str | None = None,
        limit: int = 5,
    ) -> list[UserMemoryHit]:
        return await self._search(
            collection_name=self._history_collection,
            tenant_id=tenant_id,
            user_id=user_id,
            query=query,
            chat_id=chat_id,
            include_global=True,
            limit=limit,
        )

    async def _search(
        self,
        *,
        collection_name: str,
        tenant_id: str,
        user_id: str,
        query: str,
        chat_id: str | None,
        include_global: bool,
        limit: int,
    ) -> list[UserMemoryHit]:
        await self._ensure_collections()
        query_vector = self._embed_text(query)
        base_conditions: list[qmodels.FieldCondition] = [
            qmodels.FieldCondition(
                key="tenant_id",
                match=qmodels.MatchValue(value=tenant_id),
            ),
            qmodels.FieldCondition(
                key="user_id",
                match=qmodels.MatchValue(value=user_id),
            ),
        ]

        if collection_name == self._history_collection and chat_id:
            response = await self._query_points(
                collection_name=collection_name,
                query_vector=query_vector,
                conditions=[
                    *base_conditions,
                    qmodels.FieldCondition(
                        key="session_id",
                        match=qmodels.MatchValue(value=chat_id),
                    ),
                ],
                limit=limit,
            )
            return [self._to_hit(point) for point in response.points]

        if collection_name == self._docs_collection and chat_id:
            scoped_ids = [chat_id]
            if include_global:
                scoped_ids.append("__global__")

            aggregated: list[Any] = []
            seen_point_ids: set[str] = set()
            for scoped_id in scoped_ids:
                response = await self._query_points(
                    collection_name=collection_name,
                    query_vector=query_vector,
                    conditions=[
                        *base_conditions,
                        qmodels.FieldCondition(
                            key="chat_id",
                            match=qmodels.MatchValue(value=scoped_id),
                        ),
                    ],
                    limit=limit,
                )
                for point in response.points:
                    point_id = str(point.id)
                    if point_id in seen_point_ids:
                        continue
                    seen_point_ids.add(point_id)
                    aggregated.append(point)

            aggregated.sort(key=lambda point: float(point.score), reverse=True)
            return [self._to_hit(point) for point in aggregated[:limit]]

        response = await self._query_points(
            collection_name=collection_name,
            query_vector=query_vector,
            conditions=base_conditions,
            limit=limit,
        )
        return [self._to_hit(point) for point in response.points]

    async def _ensure_collections(self) -> None:
        if self._collections_ready:
            return

        await self._ensure_collection(self._docs_collection)
        await self._ensure_collection(self._history_collection)
        self._collections_ready = True

    async def _ensure_collection(self, name: str) -> None:
        exists = await self._client.collection_exists(name)
        if not exists:
            await self._client.create_collection(
                collection_name=name,
                vectors_config=qmodels.VectorParams(
                    size=self._vector_size,
                    distance=qmodels.Distance.COSINE,
                ),
            )
            await self._client.create_payload_index(
                collection_name=name,
                field_name="tenant_id",
                field_schema=qmodels.PayloadSchemaType.KEYWORD,
            )
            await self._client.create_payload_index(
                collection_name=name,
                field_name="user_id",
                field_schema=qmodels.PayloadSchemaType.KEYWORD,
            )
            await self._client.create_payload_index(
                collection_name=name,
                field_name="chat_id",
                field_schema=qmodels.PayloadSchemaType.KEYWORD,
            )
            await self._client.create_payload_index(
                collection_name=name,
                field_name="session_id",
                field_schema=qmodels.PayloadSchemaType.KEYWORD,
            )

    async def _query_points(
        self,
        *,
        collection_name: str,
        query_vector: list[float],
        conditions: list[qmodels.FieldCondition],
        limit: int,
    ) -> Any:
        return await self._client.query_points(
            collection_name=collection_name,
            query=query_vector,
            query_filter=qmodels.Filter(must=conditions),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )

    def _to_hit(self, point: qmodels.ScoredPoint | Any) -> UserMemoryHit:
        payload = point.payload or {}
        created_at = payload.get("created_at")
        parsed_created_at: datetime | None = None
        if isinstance(created_at, str):
            try:
                parsed_created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError:
                parsed_created_at = None

        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        return UserMemoryHit(
            point_id=str(point.id),
            score=float(point.score),
            content=str(payload.get("content", "")),
            source=payload.get("source"),
            created_at=parsed_created_at,
            metadata=metadata,
        )

    def _embed_text(self, text: str) -> list[float]:
        # Deterministic fallback embedding for local/dev; replace with a production
        # embedding model provider in enterprise deployments.
        seed = int.from_bytes(sha256(text.encode("utf-8")).digest()[:8], "big")
        rand = Random(seed)
        vector = [rand.uniform(-1.0, 1.0) for _ in range(self._vector_size)]
        magnitude = sqrt(sum(v * v for v in vector)) or 1.0
        return [v / magnitude for v in vector]
