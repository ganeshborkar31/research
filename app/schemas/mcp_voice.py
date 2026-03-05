from typing import Literal

from pydantic import BaseModel, Field


class MCPClientEvent(BaseModel):
    type: str = Field(min_length=1, max_length=64)


class MCPSessionStartIn(MCPClientEvent):
    type: Literal["session.start"] = "session.start"
    session_id: str | None = Field(default=None, max_length=128)
    agent_id: str | None = Field(default=None, max_length=64)


class MCPAgentSetIn(MCPClientEvent):
    type: Literal["agent.set"] = "agent.set"
    agent_id: str = Field(min_length=1, max_length=64)


class MCPInputTextIn(MCPClientEvent):
    type: Literal["input.text"] = "input.text"
    text: str = Field(min_length=1, max_length=12000)


class MCPInputAudioIn(MCPClientEvent):
    type: Literal["input.audio"] = "input.audio"
    audio_b64: str = Field(min_length=1)
    mime_type: str = Field(default="audio/pcm", min_length=3, max_length=128)
