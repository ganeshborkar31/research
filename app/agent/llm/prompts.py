"""Reusable, versioned prompt templates with strict variable validation."""

from __future__ import annotations

import string
from dataclasses import dataclass
from typing import Mapping

from app.agent.llm.chat_model import ChatMessage


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    name: str
    version: str
    system: str
    user: str

    @property
    def variables(self) -> frozenset[str]:
        formatter = string.Formatter()
        names = set()
        for template in (self.system, self.user):
            for _, name, _, _ in formatter.parse(template):
                if name is not None:
                    if not name.isidentifier():
                        raise ValueError(f"Malformed prompt variable: {name}")
                    names.add(name)
        return frozenset(names)

    def render(self, values: Mapping[str, object]) -> list[ChatMessage]:
        expected = self.variables
        missing = expected - values.keys()
        extra = values.keys() - expected
        if missing:
            raise ValueError(f"Missing prompt variables: {', '.join(sorted(missing))}")
        if extra:
            raise ValueError(f"Unexpected prompt variables: {', '.join(sorted(extra))}")
        safe = {key: str(value) for key, value in values.items()}
        return [ChatMessage("system", self.system.format_map(safe)), ChatMessage("user", self.user.format_map(safe))]


RETRIEVAL_CHAT_V1 = PromptTemplate(
    name="retrieval_chat", version="1",
    system="{system_prompt}\n\nRetrieved context:\n{retrieval_context}\n\nUse the context when relevant, otherwise answer normally. Cite retrieved sources when provided.",
    user="Conversation so far:\n{history_text}\n\nUser message:\n{user_message}",
)


def render_retrieval_chat(*, system_prompt: str, user_message: str,
                          history: list[dict[str, str]], retrieval_context: str) -> list[ChatMessage]:
    history_text = "No prior messages." if not history else "\n".join(
        f"{item.get('role', 'user')}: {item.get('content', '')}" for item in history[-30:]
    )
    return RETRIEVAL_CHAT_V1.render({
        "system_prompt": system_prompt,
        "user_message": user_message,
        "history_text": history_text,
        "retrieval_context": retrieval_context or "No additional context.",
    })
