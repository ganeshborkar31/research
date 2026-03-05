import json

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
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import (
    get_access_claims_from_token,
    get_bearer_token_from_header,
    get_current_access_claims,
)
from app.db.session import get_db
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
from app.services.chat_service import get_chat_service
from app.services.document_service import get_document_service


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
        tenant_id=claims["tenant_id"],
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
        tenant_id=claims["tenant_id"],
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
        tenant_id=claims["tenant_id"],
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
        tenant_id=claims["tenant_id"],
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
        tenant_id=claims["tenant_id"],
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
        tenant_id=claims["tenant_id"],
        user_id=claims["sub"],
    )
    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found.")

    messages = await service.list_messages(
        db,
        chat_id=chat_id,
        tenant_id=claims["tenant_id"],
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
        tenant_id=claims["tenant_id"],
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
    status_code=status.HTTP_201_CREATED,
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
        tenant_id=claims["tenant_id"],
        user_id=claims["sub"],
    )
    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found.")

    try:
        ingest_result = await get_document_service().ingest_text(
            tenant_id=claims["tenant_id"],
            user_id=claims["sub"],
            chat_id=chat_id,
            content=payload.content,
            source=payload.source,
            metadata=payload.metadata,
            document_id=payload.document_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return ChatDocumentIngestResponse(
        chat_id=chat_id,
        document_id=ingest_result.document_id,
        source=ingest_result.source,
        chunk_count=ingest_result.chunk_count,
        total_characters=ingest_result.total_characters,
        created_at=ingest_result.created_at,
    )


@router.post(
    "/{chat_id}/documents/upload",
    response_model=ChatDocumentIngestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_chat_document_file(
    chat_id: str,
    file: UploadFile = File(...),
    source: str | None = Form(default=None),
    document_id: str | None = Form(default=None),
    metadata_json: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(get_current_access_claims),
) -> ChatDocumentIngestResponse:
    chat_service = get_chat_service()
    chat = await chat_service.get_chat(
        db,
        chat_id=chat_id,
        tenant_id=claims["tenant_id"],
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

    file_bytes = await file.read()
    try:
        ingest_result = await get_document_service().ingest_file(
            tenant_id=claims["tenant_id"],
            user_id=claims["sub"],
            chat_id=chat_id,
            file_bytes=file_bytes,
            filename=file.filename,
            content_type=file.content_type,
            source=source,
            metadata=metadata,
            document_id=document_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return ChatDocumentIngestResponse(
        chat_id=chat_id,
        document_id=ingest_result.document_id,
        source=ingest_result.source,
        chunk_count=ingest_result.chunk_count,
        total_characters=ingest_result.total_characters,
        created_at=ingest_result.created_at,
    )


@router.get("/{chat_id}/documents/search", response_model=ChatDocumentSearchResponse)
async def search_chat_documents(
    chat_id: str,
    query: str = Query(min_length=1, max_length=500),
    limit: int = Query(default=5, ge=1, le=20),
    include_global: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(get_current_access_claims),
) -> ChatDocumentSearchResponse:
    chat_service = get_chat_service()
    chat = await chat_service.get_chat(
        db,
        chat_id=chat_id,
        tenant_id=claims["tenant_id"],
        user_id=claims["sub"],
    )
    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found.")

    try:
        hits = await get_document_service().search_documents(
            tenant_id=claims["tenant_id"],
            user_id=claims["sub"],
            chat_id=chat_id,
            query=query,
            limit=limit,
            include_global=include_global,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return ChatDocumentSearchResponse(
        chat_id=chat_id,
        query=query,
        hits=[ChatDocumentSearchHit.model_validate(hit) for hit in hits],
    )


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
        tenant_id=claims["tenant_id"],
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
