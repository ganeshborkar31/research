# Knowledge AI Postman assets

These files are generated from the registered FastAPI routes in this repository:

- `RAG_API.postman_collection.json`
- `RAG_Development.postman_environment.json`

## Import

1. In Postman, choose **Import** and import both JSON files.
2. Select **RAG Development** as the active environment.
3. Set `base_url` to the backend HTTP origin, for example `http://localhost:8000`.
4. Set `ws_base_url` to the WebSocket origin, for example `ws://localhost:8000`.

The environment contains blank token and credential placeholders. No real credentials or secrets are included.

## Authentication

The implemented auth routes are:

- `POST /api/v1/auth/signup`
- `POST /api/v1/auth/request-otp`
- `POST /api/v1/auth/verify-otp`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/login/form`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout`

For a normal test run:

1. Configure `tenant_id`, `username_or_email`, and `password`.
2. Run **Authentication → Login - JSON**. The password must satisfy the backend schema minimum of eight characters, and the account must already be verified.
3. The test script saves `access_token` as a Postman collection variable. It also reads the `refresh_token` HttpOnly response cookie when Postman exposes it and saves it as a collection variable.
4. Protected requests use Bearer authentication with `{{access_token}}`.
5. **Refresh token** sends `refresh_token` in JSON and also benefits from Postman's cookie jar. The API rotates the refresh token and sets a replacement cookie.
6. **Logout** revokes the supplied token or cookie and clears the cookie.

The token response body is:

```json
{
  "access_token": "...",
  "token_type": "bearer",
  "expires_in": 900
}
```

The refresh token is deliberately not returned in the JSON response by the backend.

The OAuth2 form login expects `username` as either `username_or_email` or `tenant_id|username_or_email`, plus `password`.

## Recommended API testing sequence

1. **Operations → Health** and **Readiness**.
2. Authenticate with **Authentication → Login - JSON**.
3. Create or list a user if the test account has the required tenant permissions.
4. **Chats and RAG → Create chat**. Its response saves `chat_id`.
5. **Ingest document text** or **Ingest document file**. The returned `document_id` is saved automatically.
6. **Search chat documents** to verify vector retrieval.
7. **Send chat message** to exercise the general chat RAG pipeline.
8. **List chat messages** to inspect persisted conversation history.
9. **Support → Support chat** to exercise support policy and optional knowledge-base retrieval. Its support `chat_id` is saved as `conversation_id`.
10. Use the WebSocket requests for streaming behavior.
11. Run Twilio callback requests only when the backend is configured for Twilio or when callback behavior is being tested with stubs.

## Document ingestion and retrieval

There are two implemented ingestion routes:

- `POST /api/v1/chats/{chat_id}/documents/text`: JSON body with `content`, optional `document_id`, `source`, and object `metadata`.
- `POST /api/v1/chats/{chat_id}/documents/upload`: multipart form with required `file` and optional `source`, `document_id`, and JSON object string `metadata_json`.

After ingestion, run **Search chat documents**. Its query parameters are:

- `query`: required, 1–500 characters
- `limit`: 1–20, default 5
- `include_global`: boolean, default true

Chat messages are sent through `POST /api/v1/chats/{chat_id}/messages`. The response contains `chat_id`, `user_message`, and `assistant_message`.

## Support and voice

The support REST routes are:

- `POST /api/v1/support/chat`: `message` is required; `chat_id` and `kb_user_id` are optional.
- `POST /api/v1/support/voice`: accepts `text` or base64 `audio_b64`; `mime_type`, `return_audio`, `chat_id`, and `kb_user_id` are supported.

The live voice agent list is available at `GET /api/v1/live-chat/voice/agents`.

## WebSocket testing

The collection contains four WebSocket items:

- `/api/v1/chats/{chat_id}/ws`
- `/api/v1/support/chat/ws`
- `/api/v1/support/voice/ws`
- `/api/v1/live-chat/voice/ws`

Postman can import the `ws://` URLs and the Authorization header. Open a request, connect, and send JSON frames from the WebSocket message panel. Collection item bodies provide first-frame examples for reference; Postman does not automatically send them as frames on connection.

### General chat WebSocket

Connect to `/api/v1/chats/{chat_id}/ws` with the Bearer token. Send:

```json
{"message":"Summarize the documents attached to this chat."}
```

Expected sequence:

```json
{"type":"start","chat_id":"..."}
{"type":"chunk","content":"..."}
{"type":"done","chat_id":"..."}
```

The server may send multiple `chunk` messages. Empty messages produce an `error` event. Unauthorized and missing-chat closes use codes 4401 and 4404.

### Support chat WebSocket

Connect to `/api/v1/support/chat/ws`. The optional `chat_id` query parameter resumes a support session. Send:

```json
{"type":"input.text","text":"How do I upload a document?"}
```

The server sends `session.started`, then `response.started`, zero or more `llm.chunk`, and `response.done`. It also supports `ping`/ `pong` and `session.stop`.

### Support voice WebSocket

Connect to `/api/v1/support/voice/ws`. Supported frames are:

- `session.start` with optional `session_id`
- `input.text` with `text`
- `input.audio` with base64 `audio_b64` and `mime_type`
- `ping`
- `session.stop`

Text responses use `response.started`, `llm.chunk`, optional `tts.chunk`, and `response.done`. Audio responses first include `stt.final`.

### Live voice MCP WebSocket

Connect to `/api/v1/live-chat/voice/ws`. The server begins with `session.started` and protocol `mcp.voice.v1`. It accepts `session.start`, `agent.set`, `input.text`, `input.audio`, `ping`, and `session.stop`. MCP response event details are service-defined and are described in the collection request.

For all WebSockets, the implementation accepts the access token from the Authorization header. It also supports a `token` query parameter for clients that cannot send headers; using the header is preferred.

## SSE

No Server-Sent Events endpoint is implemented in the current backend. Searches of the application found no `EventSourceResponse`, `text/event-stream`, or SSE route. RAG streaming is exposed through WebSockets, so there is no accurate SSE request to include in the collection.

## Twilio callbacks

The Twilio routes include authenticated outbound operations:

- `POST /api/v1/twilio/sms/send`
- `POST /api/v1/twilio/voice/call`

They also include unauthenticated webhook callbacks that return `application/xml` TwiML:

- `POST /api/v1/twilio/voice/incoming`
- `POST /api/v1/twilio/voice/respond`
- `GET /api/v1/twilio/voice/outbound/twiml`
- `POST /api/v1/twilio/voice/outbound/twiml`
- `POST /api/v1/twilio/sms/incoming`

The callback requests use URL-encoded form data matching the implementation. They require a publicly reachable callback URL for real Twilio traffic; local Postman calls only exercise the route directly.

## Validation notes

The collection includes the application routes, FastAPI's generated `/openapi.json`, the Prometheus `/metrics` route, and FastAPI's `/docs`, `/docs/oauth2-redirect`, and `/redoc` routes.

The collection test scripts:

- check implemented status codes and JSON content types where applicable;
- save chat, document, support conversation, and user IDs;
- save JWT access claims and refresh cookies after authentication;
- clear stored tokens after logout.

Provider-backed operations such as RAG generation, Qdrant retrieval, SMTP OTP delivery, and Twilio calls still require those services and configuration to be available.

