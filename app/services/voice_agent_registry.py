from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceAgentProfile:
    agent_id: str
    name: str
    description: str
    system_prompt: str
    domain: str


_PROFILES: dict[str, VoiceAgentProfile] = {
    "general": VoiceAgentProfile(
        agent_id="general",
        name="General Assistant",
        description="General purpose real-time assistant for daily conversations.",
        domain="general",
        system_prompt=(
            "You are a real-time voice assistant. "
            "Respond naturally, briefly, and with clear spoken-style wording."
        ),
    ),
    "interview": VoiceAgentProfile(
        agent_id="interview",
        name="Interview Coach",
        description="Helps with interviews using structured, practical guidance.",
        domain="career",
        system_prompt=(
            "You are an interview coach voice assistant. "
            "Ask focused follow-up questions, give concise feedback, and suggest actionable improvements."
        ),
    ),
    "counseling": VoiceAgentProfile(
        agent_id="counseling",
        name="Supportive Listener",
        description="Provides supportive conversation guidance with empathetic tone.",
        domain="wellbeing",
        system_prompt=(
            "You are a supportive counseling-style voice assistant. "
            "Be empathetic, calm, and non-judgmental while staying practical and concise."
        ),
    ),
}


def list_voice_agent_profiles() -> list[VoiceAgentProfile]:
    return list(_PROFILES.values())


def get_voice_agent_profile(agent_id: str) -> VoiceAgentProfile | None:
    return _PROFILES.get(agent_id)


def resolve_voice_agent_profile(agent_id: str | None) -> VoiceAgentProfile:
    if agent_id:
        profile = get_voice_agent_profile(agent_id)
        if profile:
            return profile
    return _PROFILES["general"]
