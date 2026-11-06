import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.retrieval import router
from app.db.session import get_db
from app.rag.models.retrieval import AssembledContext, RetrievalResult
from app.rag.retrieval.dependencies import get_retrieval_service
from app.rag.retrieval.query_processor import RetrievalAccessDenied
from app.services.jwt_service import create_access_token


class FakeRetrievalService:
    def __init__(self, *, denied=False):
        self.denied = denied
        self.user_ids = []

    async def retrieve(self, request, *, authenticated_user_id, session):
        self.user_ids.append(authenticated_user_id)
        if self.denied:
            raise RetrievalAccessDenied("denied")
        result = RetrievalResult(
            document_id="d1", document_version_id="v1", chunk_id="c1", content="secret passage",
            score=0.9, knowledge_base_id=request.knowledge_base_id, source_ref="source.pdf",
        )
        return [result], AssembledContext(chunks=(), text="secret passage", char_count=14, token_count=4)


async def make_client(service):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/retrieval")

    async def fake_db():
        yield object()

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_retrieval_service] = lambda: service
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_api_requires_auth_derives_identity_and_returns_sources():
    service = FakeRetrievalService()
    token = create_access_token("user-a").token
    async with await make_client(service) as client:
        unauthorized = await client.post("/api/v1/retrieval/", json={"query": "q", "knowledge_base_id": "kb1"})
        response = await client.post(
            "/api/v1/retrieval/", headers={"Authorization": f"Bearer {token}"},
            json={"query": "q", "knowledge_base_id": "kb1"},
        )
    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert service.user_ids == ["user-a"]
    assert response.json()["results"][0]["source_ref"] == "source.pdf"


@pytest.mark.asyncio
async def test_api_hides_unauthorized_knowledge_base_and_validates_body():
    service = FakeRetrievalService(denied=True)
    token = create_access_token("user-a").token
    async with await make_client(service) as client:
        denied = await client.post(
            "/api/v1/retrieval/", headers={"Authorization": f"Bearer {token}"},
            json={"query": "q", "knowledge_base_id": "foreign"},
        )
        invalid = await client.post(
            "/api/v1/retrieval/", headers={"Authorization": f"Bearer {token}"},
            json={"query": " ", "knowledge_base_id": "kb"},
        )
    assert denied.status_code == 404
    assert invalid.status_code == 422
