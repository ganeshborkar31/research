from pydantic import BaseModel, Field


class VoiceAgentProfileOut(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    domain: str = Field(min_length=1, max_length=64)
