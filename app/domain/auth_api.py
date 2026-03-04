from datetime import datetime
from pydantic import BaseModel, Field


class SignupRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=36)
    username: str = Field(min_length=3, max_length=120)
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    phone_e164: str | None = Field(default=None, max_length=32)
    first_name: str | None = Field(default=None, max_length=120)
    last_name: str | None = Field(default=None, max_length=120)


class LoginRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=36)
    username_or_email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)


class RequestOTPRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=36)
    username_or_email: str = Field(min_length=3, max_length=255)
    channel: str = Field(default="email", pattern="^(email|mobile)$")
    purpose: str = Field(default="signup", max_length=32)


class VerifyOTPRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=36)
    username_or_email: str = Field(min_length=3, max_length=255)
    channel: str = Field(default="email", pattern="^(email|mobile)$")
    purpose: str = Field(default="signup", max_length=32)
    otp_code: str = Field(min_length=4, max_length=12)


class RefreshRequest(BaseModel):
    refresh_token: str | None = None


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class OTPRequestedResponse(BaseModel):
    status: str = "otp_sent"
    expires_at: datetime
    channel: str
    destination: str


class LogoutResponse(BaseModel):
    status: str = "logged_out"

