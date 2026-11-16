import asyncio
import json
from pathlib import Path
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import (
    get_access_claims_from_token,
    get_bearer_token_from_header,
    get_current_access_claims,
)
from app.db.session import get_db
from app.core.config import get_settings
from app.rag.ingestion.dependencies import build_ingestion_workflow
from app.rag.ingestion.workflow import (
    IngestionOwnershipError,
    PermanentIngestionError,
)
from app.rag.models.retrieval import RetrievalRequest
from app.rag.retrieval.dependencies import get_retrieval_service
from app.rag.retrieval.query_processor import RetrievalAccessDenied, RetrievalQueryError
from app.rag.retrieval.reranking import RerankingError
from app.rag.retrieval.service import RetrievalService, RetrievalServiceError
from app.schemas.chat_api import (
    ChatCreateRequest,
    ChatMessageCreateRequest,
    ChatMessageOut,
    ChatReplyResponse,
    ChatSessionOut,
    ChatUpdateRequest,
)
from app.schemas.document_api import (
    ChatDocumentIngestResponse,
    ChatDocumentSearchHit,
    ChatDocumentSearchResponse,
    ChatDocumentTextUploadRequest,
)
from app.agent.chat_sessions import get_chat_service


router = APIRouter()


@router.post("/", response_model=ChatSessionOut, status_code=status.HTTP_201_CREATED)
async def create_chat(
    payload: ChatCreateRequest,
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(get_current_access_claims),
) -> ChatSessionOut:
    service = get_chat_service()
    chat = await service.create_chat(
        db,
        user_id=claims["sub"],
        title=payload.title,
    )
    return ChatSessionOut.model_validate(chat)


@router.get("/", response_model=list[ChatSessionOut])
async def list_chats(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(get_current_access_claims),
) -> list[ChatSessionOut]:
    service = get_chat_service()
    chats = await service.list_chats(
        db,
        user_id=claims["sub"],
        limit=limit,
        offset=offset,
    )
    return [ChatSessionOut.model_validate(chat) for chat in chats]


@router.get("/{chat_id}", response_model=ChatSessionOut)
async def get_chat(
    chat_id: str,
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(get_current_access_claims),
) -> ChatSessionOut:
    service = get_chat_service()
    chat = await service.get_chat(
        db,
        chat_id=chat_id,
        user_id=claims["sub"],
    )
    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found.")
    return ChatSessionOut.model_validate(chat)


@router.patch("/{chat_id}", response_model=ChatSessionOut)
async def rename_chat(
    chat_id: str,
    payload: ChatUpdateRequest,
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(get_current_access_claims),
) -> ChatSessionOut:
    service = get_chat_service()
    chat = await service.get_chat(
        db,
        chat_id=chat_id,
        user_id=claims["sub"],
    )
    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found.")

    updated = await service.update_chat_title(db, chat=chat, title=payload.title)
    return ChatSessionOut.model_validate(updated)


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(
    chat_id: str,
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(get_current_access_claims),
) -> None:
    service = get_chat_service()
    chat = await service.get_chat(
        db,
        chat_id=chat_id,
        user_id=claims["sub"],
    )
    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found.")
    await service.delete_chat(db, chat=chat)


@router.get("/{chat_id}/messages", response_model=list[ChatMessageOut])
async def get_chat_messages(
    chat_id: str,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(get_current_access_claims),
) -> list[ChatMessageOut]:
    service = get_chat_service()
    chat = await service.get_chat(
        db,
        chat_id=chat_id,
        user_id=claims["sub"],
    )
    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found.")

    messages = await service.list_messages(
        db,
        chat_id=chat_id,
        user_id=claims["sub"],
        limit=limit,
        offset=offset,
    )
    return [ChatMessageOut.model_validate(message) for message in messages]


@router.post("/{chat_id}/messages", response_model=ChatReplyResponse)
async def send_chat_message(
    chat_id: str,
    payload: ChatMessageCreateRequest,
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(get_current_access_claims),
) -> ChatReplyResponse:
    service = get_chat_service()
    chat = await service.get_chat(
        db,
        chat_id=chat_id,
        user_id=claims["sub"],
    )
    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found.")

    try:
        user_message, assistant_message = await service.send_message(
            db,
            chat=chat,
            message=payload.message,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return ChatReplyResponse(
        chat_id=chat_id,
        user_message=ChatMessageOut.model_validate(user_message),
        assistant_message=ChatMessageOut.model_validate(assistant_message),
    )


@router.post(
    "/{chat_id}/documents/text",
    response_model=ChatDocumentIngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_chat_document_text(
    chat_id: str,
    payload: ChatDocumentTextUploadRequest,
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(get_current_access_claims),
) -> ChatDocumentIngestResponse:
    chat_service = get_chat_service()
    chat = await chat_service.get_chat(
        db,
        chat_id=chat_id,
        user_id=claims["sub"],
    )
    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found.")

    try:
        staged_source = await _stage_source(
            content=payload.content.encode("utf-8"), source_ref=payload.source_ref,
            force_text=True,
        )
        ingest_result = await _submit_document(
            user_id=claims["sub"], chat_id=chat_id,
            staged_source=staged_source, source_ref=payload.source_ref,
            metadata=payload.metadata, document_id=payload.document_id,
            idempotency_key=payload.idempotency_key,
        )
    except IngestionOwnershipError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RAG scope not found.") from exc
    except (ValueError, PermanentIngestionError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _ingest_response(chat_id, payload.source_ref, ingest_result)


@router.post(
    "/{chat_id}/documents/upload",
    response_model=ChatDocumentIngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_chat_document_file(
    chat_id: str,
    file: UploadFile = File(...),
    source_ref: str | None = Form(default=None, max_length=512),
    document_id: str | None = Form(default=None),
    idempotency_key: str | None = Form(default=None, max_length=128),
    metadata_json: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(get_current_access_claims),
) -> ChatDocumentIngestResponse:
    chat_service = get_chat_service()
    chat = await chat_service.get_chat(
        db,
        chat_id=chat_id,
        user_id=claims["sub"],
    )
    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found.")

    metadata: dict = {}
    if metadata_json:
        try:
            parsed = json.loads(metadata_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="metadata_json must be valid JSON.",
            ) from exc
        if not isinstance(parsed, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="metadata_json must be a JSON object.",
            )
        metadata = parsed

    file_bytes = await file.read(50 * 1024 * 1024 + 1)
    if len(file_bytes) > 50 * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Uploaded document is too large.")
    source_label = (source_ref or file.filename or "uploaded_document").strip()
    try:
        staged_source = await _stage_source(content=file_bytes, source_ref=source_label)
        ingest_result = await _submit_document(
            user_id=claims["sub"], chat_id=chat_id,
            staged_source=staged_source, source_ref=source_label,
            metadata={**metadata, "filename": file.filename or source_label,
                      "content_type": file.content_type or "application/octet-stream"},
            document_id=document_id, idempotency_key=idempotency_key,
        )
    except IngestionOwnershipError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RAG scope not found.") from exc
    except (ValueError, PermanentIngestionError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _ingest_response(chat_id, source_label, ingest_result)


@router.get("/{chat_id}/documents/search", response_model=ChatDocumentSearchResponse)
async def search_chat_documents(
    chat_id: str,
    query: str = Query(min_length=1, max_length=500),
    limit: int = Query(default=5, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(get_current_access_claims),
    service: RetrievalService = Depends(get_retrieval_service),
) -> ChatDocumentSearchResponse:
    chat_service = get_chat_service()
    chat = await chat_service.get_chat(
        db,
        chat_id=chat_id,
        user_id=claims["sub"],
    )
    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found.")

    try:
        results, _ = await service.retrieve(
            RetrievalRequest(
                query=query,
                knowledge_base_id=chat_id,
                top_k=limit,
                metadata_filters={"chat_id": chat_id},
            ),
            authenticated_user_id=claims["sub"], session=db,
        )
    except RetrievalAccessDenied as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RAG scope not found.") from exc
    except RetrievalQueryError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except RerankingError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Requested reranking is unavailable.") from exc
    except RetrievalServiceError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Retrieval is temporarily unavailable.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return ChatDocumentSearchResponse(
        chat_id=chat_id,
        query=query,
        hits=[ChatDocumentSearchHit(
            point_id=result.chunk_id, score=result.score, content=result.content,
            source=result.source_ref, metadata=result.metadata,
        ) for result in results],
    )


def _ingest_response(chat_id: str, source_ref: str,
                     result: dict[str, str | int]) -> ChatDocumentIngestResponse:
    return ChatDocumentIngestResponse(
        chat_id=chat_id,
        document_id=str(result["document_id"]),
        document_version_id=str(result["document_version_id"]),
        job_id=str(result["job_id"]), status=str(result["status"]),
        version_number=int(result["version_number"]), source_ref=source_ref,
    )


async def _stage_source(*, content: bytes, source_ref: str, force_text: bool = False) -> str:
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Document content cannot be empty.")
    settings = get_settings()
    root = Path(settings.rag_source_root).expanduser()
    label = Path(source_ref.replace("\\", "/")).name
    suffix = ".txt" if force_text else Path(label).suffix.lower()
    supported = {".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".md", ".markdown",
                 ".txt", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
    if suffix not in supported:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported document type.")
    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
    name = f"{uuid4().hex}{suffix}"
    target = root / name
    await asyncio.to_thread(target.write_bytes, content)
    return name


async def _submit_document(*, user_id: str, chat_id: str,
                           staged_source: str, source_ref: str, metadata: dict,
                           document_id: str | None, idempotency_key: str | None) -> dict[str, str | int]:
    workflow = None
    try:
        workflow = build_ingestion_workflow()
        return await run_in_threadpool(
            # Chat-scoped documents share an implicit RAG namespace keyed by chat ID.
            workflow.submit, user_id=user_id, knowledge_base_id=chat_id,
            source_ref=staged_source, document_id=document_id,
            idempotency_key=idempotency_key,
            metadata={**metadata, "chat_id": chat_id, "source_ref": source_ref},
        )
    finally:
        if workflow is not None:
            await workflow.close()


@router.websocket("/{chat_id}/ws")
async def chat_stream_websocket(
    websocket: WebSocket,
    chat_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    token = get_bearer_token_from_header(websocket.headers.get("authorization"))
    if not token:
        token = websocket.query_params.get("token")

    try:
        claims = get_access_claims_from_token(token or "")
    except HTTPException:
        await websocket.close(code=4401, reason="Unauthorized")
        return

    service = get_chat_service()
    chat = await service.get_chat(
        db,
        chat_id=chat_id,
        user_id=claims["sub"],
    )
    if not chat:
        await websocket.close(code=4404, reason="Chat not found")
        return

    await websocket.accept()
    try:
        while True:
            payload = await websocket.receive_json()
            message = str(payload.get("message", "")).strip()
            if not message:
                await websocket.send_json({"type": "error", "detail": "Message cannot be empty."})
                continue

            await websocket.send_json({"type": "start", "chat_id": chat_id})
            async for chunk in service.stream_message(db, chat=chat, message=message):
                await websocket.send_json({"type": "chunk", "content": chunk})
            await websocket.send_json({"type": "done", "chat_id": chat_id})
    except WebSocketDisconnect:
        return
