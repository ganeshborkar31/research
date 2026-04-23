from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import get_settings


@dataclass(frozen=True)
class SupportPolicy:
    tenant_id: str
    business_name: str
    rules: list[str]
    blocked_terms: list[str]
    escalation_message: str
    kb_user_id: str | None = None

    def system_prompt(self) -> str:
        rules_text = "\n".join(f"- {rule}" for rule in self.rules) or "- Follow business policy."
        return (
            f"You are a customer support agent for {self.business_name}.\n"
            "Follow these business policies and rules:\n"
            f"{rules_text}\n"
            "If the user asks for an action you cannot perform, explain the limitation and offer to escalate."
        )


_DEFAULT_POLICY = SupportPolicy(
    tenant_id="default",
    business_name="Knowledge AI",
    rules=[
        "Be concise, accurate, and helpful.",
        "Never request passwords or sensitive secrets.",
        "Ask clarifying questions when details are missing.",
    ],
    blocked_terms=["password", "credit card", "ssn", "social security"],
    escalation_message="I can connect you to a human agent for this request.",
)

_POLICY_CACHE: dict[str, SupportPolicy] | None = None


def resolve_support_policy(tenant_id: str) -> SupportPolicy:
    policies = _load_policies()
    return policies.get(tenant_id) or policies.get("default") or _DEFAULT_POLICY


def _load_policies() -> dict[str, SupportPolicy]:
    global _POLICY_CACHE
    if _POLICY_CACHE is not None:
        return _POLICY_CACHE

    settings = get_settings()
    policy_path = Path("config/support_policies.json")
    if hasattr(settings, "support_policy_path"):
        value = str(getattr(settings, "support_policy_path") or "").strip()
        if value:
            policy_path = Path(value)

    if not policy_path.exists():
        _POLICY_CACHE = {"default": _DEFAULT_POLICY}
        return _POLICY_CACHE

    try:
        data = json.loads(policy_path.read_text(encoding="utf-8"))
    except Exception:
        _POLICY_CACHE = {"default": _DEFAULT_POLICY}
        return _POLICY_CACHE

    policies: dict[str, SupportPolicy] = {}
    default_payload = _as_dict(data.get("default"))
    if default_payload:
        policies["default"] = _parse_policy("default", default_payload)
    for tenant_id, payload in _as_dict(data.get("tenants")).items():
        if not isinstance(payload, dict):
            continue
        policies[tenant_id] = _parse_policy(tenant_id, payload)

    if "default" not in policies:
        policies["default"] = _DEFAULT_POLICY
    _POLICY_CACHE = policies
    return policies


def _parse_policy(tenant_id: str, payload: dict[str, Any]) -> SupportPolicy:
    business_name = str(payload.get("business_name") or _DEFAULT_POLICY.business_name)
    rules = _ensure_list(payload.get("rules")) or _DEFAULT_POLICY.rules
    blocked_terms = _ensure_list(payload.get("blocked_terms")) or []
    escalation_message = str(
        payload.get("escalation_message") or _DEFAULT_POLICY.escalation_message
    )
    kb_user_id = payload.get("kb_user_id")
    kb_user_id_value = str(kb_user_id).strip() if kb_user_id else None
    return SupportPolicy(
        tenant_id=tenant_id,
        business_name=business_name,
        rules=rules,
        blocked_terms=blocked_terms,
        escalation_message=escalation_message,
        kb_user_id=kb_user_id_value,
    )


def _ensure_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}
