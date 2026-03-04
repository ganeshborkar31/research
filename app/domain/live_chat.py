from pydantic import BaseModel, Field


class LiveChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    user_id: str | None = Field(default=None, max_length=128)


class LiveChatResponse(BaseModel):
    session_id: str
    reply: str
