import pytest

from app.infra.llm.langchain_chat import LangChainChatGateway


class FakeGeminiClient:
    def __init__(self, *, generated_text: str = "generated", stream_chunks: list[str] | None = None):
        self.generated_text = generated_text
        self.stream_chunks = stream_chunks if stream_chunks is not None else []
        self.generate_calls: list[dict] = []
        self.stream_calls: list[dict] = []

    async def generate_reply(self, *, system_prompt: str, user_message: str, history):
        self.generate_calls.append(
            {
                "system_prompt": system_prompt,
                "user_message": user_message,
                "history": history,
            }
        )
        return self.generated_text

    async def stream_reply(self, *, system_prompt: str, user_message: str, history):
        self.stream_calls.append(
            {
                "system_prompt": system_prompt,
                "user_message": user_message,
                "history": history,
            }
        )
        for chunk in self.stream_chunks:
            yield chunk


@pytest.mark.asyncio
async def test_generate_reply_uses_langchain_prompt_template():
    fake = FakeGeminiClient(generated_text="hello from llm")
    gateway = LangChainChatGateway(gemini_client=fake)  # type: ignore[arg-type]
    captured_payload: dict[str, str] = {}

    class StubChain:
        async def ainvoke(self, payload):
            captured_payload.update(payload)
            return "hello from llm"

    gateway._chain = StubChain()

    reply = await gateway.generate_reply(
        system_prompt="Be precise.",
        user_message="What is RAG?",
        history=[{"role": "user", "content": "Hi"}],
        retrieval_context="[doc] RAG combines retrieval and generation.",
    )

    assert reply == "hello from llm"
    assert captured_payload["system_prompt"] == "Be precise."
    assert captured_payload["user_message"] == "What is RAG?"
    assert "user: Hi" in captured_payload["history_text"]
    assert "RAG combines retrieval and generation." in captured_payload["retrieval_context"]


@pytest.mark.asyncio
async def test_stream_reply_prefers_provider_streaming():
    fake = FakeGeminiClient(stream_chunks=["Hel", "lo", " ", "world"])
    gateway = LangChainChatGateway(gemini_client=fake)  # type: ignore[arg-type]

    chunks: list[str] = []
    async for chunk in gateway.stream_reply(
        system_prompt="Be brief.",
        user_message="Say hello",
        history=[],
        retrieval_context="",
    ):
        chunks.append(chunk)

    assert chunks == ["Hel", "lo", " ", "world"]
    assert len(fake.stream_calls) == 1
    assert len(fake.generate_calls) == 0


@pytest.mark.asyncio
async def test_stream_reply_falls_back_when_provider_stream_is_empty():
    fake = FakeGeminiClient(generated_text="fallback full reply", stream_chunks=[])
    gateway = LangChainChatGateway(gemini_client=fake)  # type: ignore[arg-type]

    chunks: list[str] = []
    async for chunk in gateway.stream_reply(
        system_prompt="Be brief.",
        user_message="Say hello",
        history=[],
        retrieval_context="",
    ):
        chunks.append(chunk)

    assert "".join(chunks) == "fallback full reply"
    assert len(fake.stream_calls) == 1
    assert len(fake.generate_calls) == 1
