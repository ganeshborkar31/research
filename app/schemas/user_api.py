from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class UserCreate(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=36)
    username: str = Field(min_length=3, max_length=120)
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    external_user_id: str | None = Field(default=None, max_length=128)
    first_name: str | None = Field(default=None, max_length=120)
    last_name: str | None = Field(default=None, max_length=120)
    phone_e164: str | None = Field(default=None, max_length=32)
    department: str | None = Field(default=None, max_length=120)
    title: str | None = Field(default=None, max_length=120)
    timezone: str | None = Field(default=None, max_length=64)
    status: str = Field(default="active", max_length=32)
    is_verified: bool = False
    personal_data: dict[str, Any] = Field(default_factory=dict)
    preferences: dict[str, Any] = Field(default_factory=dict)
    compliance_flags: dict[str, Any] = Field(default_factory=dict)


class UserUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=120)
    email: str | None = Field(default=None, min_length=5, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    external_user_id: str | None = Field(default=None, max_length=128)
    first_name: str | None = Field(default=None, max_length=120)
    last_name: str | None = Field(default=None, max_length=120)
    phone_e164: str | None = Field(default=None, max_length=32)
    department: str | None = Field(default=None, max_length=120)
    title: str | None = Field(default=None, max_length=120)
    timezone: str | None = Field(default=None, max_length=64)
    status: str | None = Field(default=None, max_length=32)
    is_verified: bool | None = None
    personal_data: dict[str, Any] | None = None
    preferences: dict[str, Any] | None = None
    compliance_flags: dict[str, Any] | None = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    username: str
    email: str
    external_user_id: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone_e164: str | None = None
    department: str | None = None
    title: str | None = None
    timezone: str | None = None
    status: str
    is_verified: bool
    email_verified_at: datetime | None = None
    phone_verified_at: datetime | None = None
    personal_data: dict[str, Any]
    preferences: dict[str, Any]
    compliance_flags: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime | None = None


class UserDeleteOut(BaseModel):
    status: str = "deleted"
    user_id: str
