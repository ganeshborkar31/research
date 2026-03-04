from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
from jwt import InvalidTokenError

from app.core.config import get_settings


@dataclass
class AccessToken:
    token: str
    expires_in_seconds: int


@dataclass
class RefreshToken:
    token: str
    jti: str
    expires_at: datetime


def create_access_token(user_id: str, tenant_id: str) -> AccessToken:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_delta = timedelta(minutes=settings.access_token_exp_minutes)
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "token_type": "access",
        "iat": now,
        "exp": now + expires_delta,
        "jti": str(uuid4()),
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return AccessToken(token=token, expires_in_seconds=int(expires_delta.total_seconds()))


def create_refresh_token(user_id: str, tenant_id: str) -> RefreshToken:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_delta = timedelta(days=settings.refresh_token_exp_days)
    expires_at = now + expires_delta
    jti = str(uuid4())
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "token_type": "refresh",
        "iat": now,
        "exp": expires_at,
        "jti": jti,
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return RefreshToken(token=token, jti=jti, expires_at=expires_at)


def decode_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )


def safe_decode_token(token: str) -> dict | None:
    try:
        return decode_token(token)
    except InvalidTokenError:
        return None

