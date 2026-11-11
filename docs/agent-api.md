# Production agent API

The endpoints below are mounted under `/api/v1/agent`. They use the authenticated
application bearer token and the production runtime in `app/agent/`. The caller's
identity always comes from the access token. Model/provider selection is configured
on the server; request bodies reject unknown fields, including model and user IDs.

## Turn request

`POST /turn` returns the canonical `AgentResponse`. Omit `conversation_id` to
create an agent conversation. Include `knowledge_base_id` to enable authorized
retrieval; otherwise the turn is normal chat. Retrieval document filters and
strategy are optional.

```json
{
  "message": "What is the retention period?",
  "conversation_id": null,
  "knowledge_base_id": "kb-123",
  "response": {"temperature": 0.2, "max_output_tokens": 2048},
  "retrieval": {"top_k": 5, "strategy": "hybrid", "document_ids": []}
}
```

`max_output_tokens` is capped by server configuration (2,048 by default). The
generation deadline defaults to 120 seconds. A caller may pass `X-Request-ID`
(up to 128 safe characters); otherwise the server creates one. The response
contains that ID, and the same ID is returned in the response header.

```json
{
  "request_id": "req-7f21",
  "conversation_id": "conv-123",
  "message_id": "msg-456",
  "status": "completed",
  "assistant_output": "The retention period is 30 days [1].",
  "citations": [{
    "citation_number": 1,
    "document_id": "doc-1",
    "document_version_id": "ver-2",
    "chunk_id": "chunk-9",
    "page_numbers": [4],
    "section_reference": ["Retention"],
    "source_ref": "policy.pdf",
    "source_metadata": {"score": 0.91},
    "relevance_score": 0.91
  }],
  "tool_executions": [],
  "token_usage": null,
  "model": "server-configured-model",
  "finish_reason": "stop",
  "error": null
}
```

Execution failures use the same `AgentResponse` shape and a safe `error` with
`code`, `message`, `category`, `retryable`, and `request_reference`. Invalid
request schemas use FastAPI's standard 422 validation response. Foreign, missing,
and archived conversation IDs return 404.

## Streaming

`POST /turn/stream` accepts the same JSON body as `/turn` and returns
`text/event-stream`; browser clients can consume it with `fetch()`. Every `data`
line is the serialized `AgentEvent` envelope with `event_id`, `request_id`,
`conversation_id`, `event_type`, `sequence`, `timestamp`, and typed `data`.

Supported event types and relevant `data` fields:

| Event | Payload fields |
|---|---|
| `request.started` | `phase` |
| `conversation.loaded` | `message_id` |
| `retrieval.started` | `phase` |
| `retrieval.completed` | `tool`, `citations` |
| `tool.started` | `phase` |
| `tool.completed` | `tool` |
| `message.started` | `message_id` |
| `message.delta` | `text` |
| `message.completed` | `message_id`, `text` |
| `request.completed` | canonical `response` |
| `request.failed` | safe `error` and, when available, canonical `response` |

Example frames:

```text
event: message.delta
id: evt-2
data: {"event_id":"evt-2","request_id":"req-7f21","conversation_id":"conv-123","event_type":"message.delta","sequence":8,"timestamp":"2026-11-11T12:00:00Z","data":{"text":"The retention period is [1]."}}

event: request.completed
id: evt-3
data: {"event_id":"evt-3","request_id":"req-7f21","conversation_id":"conv-123","event_type":"request.completed","sequence":9,"timestamp":"2026-11-11T12:00:01Z","data":{"response":{"request_id":"req-7f21","conversation_id":"conv-123","message_id":"msg-456","status":"completed","assistant_output":"The retention period is 30 days [1].","citations":[{"citation_number":1,"document_id":"doc-1","document_version_id":"ver-2","chunk_id":"chunk-9","page_numbers":[4],"section_reference":["Retention"],"source_ref":"policy.pdf","source_metadata":{"score":0.91},"relevance_score":0.91}],"tool_executions":[],"token_usage":null,"model":"server-configured-model","finish_reason":"stop","error":null}}}
```

Clients should regard only `request.completed` as successful completion. The
server observes disconnects and cancels the runtime; partial assistant output is
not persisted as a completed answer.

## Conversation operations

- `GET /conversations/{conversation_id}` retrieves an active, owned agent conversation.
- `GET /conversations/{conversation_id}/history?limit=100&before_sequence=42` returns
  its ordered messages. `limit` is 1–200; `before_sequence` is optional.
- `DELETE /conversations/{conversation_id}` applies the existing soft archive
  behavior and returns 204. It does not hard-delete data.

All operations return 404 for foreign, missing, archived, or non-agent
conversations. The regular `/api/v1/chats` API continues to operate on regular
chat conversations.
