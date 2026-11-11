import pytest

from app.agent import ContextLimitError, ContextManager, ContextMessage


def test_context_preserves_roles_order_and_includes_retrieval_and_tools():
    manager = ContextManager(max_tokens=100, max_history_messages=2, token_counter=lambda text: len(text))
    result = manager.prepare(
        system_prompt="rules", current_user_message="now",
        history=[ContextMessage("user", "old"), ContextMessage("assistant", "answer"),
                 ContextMessage("user", "recent")],
        retrieved_context="facts", tool_results=[ContextMessage("tool", "tool output", "lookup")],
    )
    assert [(message.role, message.content) for message in result] == [
        ("system", "rules"), ("system", "Retrieved context:\nfacts"),
        ("assistant", "answer"), ("user", "recent"), ("user", "now"), ("tool", "tool output"),
    ]


def test_context_drops_old_history_to_fit_limits_and_rejects_oversized_fixed_context():
    manager = ContextManager(max_tokens=7, max_history_messages=10, token_counter=len)
    messages = manager.prepare(system_prompt="s", current_user_message="q", history=[
        ContextMessage("user", "12345"), ContextMessage("assistant", "a")
    ])
    assert [message.content for message in messages] == ["s", "a", "q"]
    with pytest.raises(ContextLimitError):
        manager.prepare(system_prompt="system too long", current_user_message="q")

