from collections.abc import AsyncIterator
import pytest

from app.core.config import Settings
from app.agent.llm.chat_model import ChatMessage, ChatModelConfig, ChatProviderSelectionError, GenerationOptions, normalize_usage
from app.agent.llm.prompts import PromptTemplate, render_retrieval_chat
from app.agent.llm.providers import GeminiChatModel, build_chat_model


def settings(**values):
    base = dict(database_url="postgresql://example:example@localhost/db", redis_url="redis://localhost",
                qdrant_url="http://localhost", rabbitmq_url="amqp://localhost", llm_provider="gemini")
    base.update(values)
    return Settings(_env_file=None, **base)


def test_provider_selection_and_model_configuration():
    selected = object()
    model = build_chat_model(
        settings(),
        config=ChatModelConfig(model="custom-gemini"),
        provider_builders={"gemini": lambda _settings, config: (selected, config.model)},
    )
    assert model == (selected, "custom-gemini")
    with pytest.raises(ChatProviderSelectionError):
        build_chat_model(settings(), config=ChatModelConfig(provider="unknown"))


def test_default_gemini_provider_uses_transport_without_experimental_gateway(monkeypatch):
    class Client:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr("app.agent.llm.gemini_client.GeminiClient", Client)
    model = build_chat_model(settings(gemini_api_key="test-key"))
    assert isinstance(model, GeminiChatModel)
    assert model.model == "gemini-2.5-flash"
    assert model.client.kwargs == {
        "api_key": "test-key", "model": "gemini-2.5-flash", "timeout_seconds": 30,
    }


def test_openai_selection_requires_key():
    with pytest.raises(ChatProviderSelectionError, match="OPENAI_API_KEY"):
        build_chat_model(settings(), config=ChatModelConfig(provider="openai"))


def test_usage_normalization_handles_openai_and_google_shapes():
    assert normalize_usage({"prompt_tokens": 3, "completion_tokens": 2}) == normalize_usage(
        {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5})


@pytest.mark.asyncio
async def test_gemini_message_conversion_and_streaming():
    class Fake:
        async def generate_reply(self, **kwargs):
            self.args = kwargs
            return "ok"

        async def stream_reply(self, **kwargs) -> AsyncIterator[str]:
            self.args = kwargs
            yield "o"
            yield "k"

    fake = Fake()
    model = GeminiChatModel(fake, "gemini-test")  # type: ignore[arg-type]
    messages = [ChatMessage("system", "rules"), ChatMessage("user", "question"), ChatMessage("assistant", "prior"), ChatMessage("user", "next")]
    response = await model.generate(messages, options=GenerationOptions(temperature=0.2))
    assert response.content == "ok"
    assert fake.args == {"system_prompt": "rules", "user_message": "next", "history": [{"role": "user", "content": "question"}, {"role": "assistant", "content": "prior"}]}
    assert [c.content async for c in model.stream(messages)] == ["o", "k"]


@pytest.mark.asyncio
async def test_gemini_provider_failure_is_normalized():
    class Failing:
        async def generate_reply(self, **_kwargs):
            raise TimeoutError("private provider detail")

    from app.agent.llm.chat_model import ChatProviderError
    with pytest.raises(ChatProviderError, match="Gemini generation failed"):
        await GeminiChatModel(Failing(), "test").generate([ChatMessage("user", "hi")])  # type: ignore[arg-type]


def test_prompt_rendering_has_context_and_rejects_missing_variables():
    messages = render_retrieval_chat(system_prompt="be concise", user_message="question", history=[], retrieval_context="[1] source")
    assert messages[0].role == "system" and "[1] source" in messages[0].content
    assert messages[1].role == "user" and "question" in messages[1].content
    with pytest.raises(ValueError, match="Missing prompt variables"):
        PromptTemplate("broken", "1", "{instruction}", "{question}").render({"instruction": "go"})

