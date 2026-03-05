from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.schemas.auth_api import LoginRequest, RequestOTPRequest, SignupRequest, VerifyOTPRequest
from app.infra.notifications import OTPDispatchError as InfraOTPDispatchError, send_otp_code
from app.models.auth import OTPChallenge, RefreshToken
from app.models.user import User
from app.services.jwt_service import create_access_token, create_refresh_token, safe_decode_token
from app.services.security import generate_otp, hash_password, hash_value, verify_password


class AuthError(Exception):
    pass


class InvalidCredentialsError(AuthError):
    pass


class OTPVerificationError(AuthError):
    pass


class OTPDeliveryError(AuthError):
    pass


@dataclass
class TokenBundle:
    access_token: str
    access_expires_in: int
    refresh_token: str
    refresh_expires_at: datetime


@dataclass
class OTPDispatchResult:
    expires_at: datetime
    channel: str
    destination: str


async def signup(db: AsyncSession, payload: SignupRequest) -> tuple[User, OTPDispatchResult]:
    user = User(
        tenant_id=payload.tenant_id,
        username=payload.username,
        email=payload.email,
        phone_e164=payload.phone_e164,
        first_name=payload.first_name,
        last_name=payload.last_name,
        password_hash=hash_password(payload.password),
        status="active",
        is_verified=False,
        personal_data={},
        preferences={},
        compliance_flags={},
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise InvalidCredentialsError("User already exists for this tenant.") from exc

    await db.refresh(user)
    otp_result = await _create_and_send_otp(
        db,
        user=user,
        channel="email",
        purpose="signup",
    )
    return user, otp_result


async def login(db: AsyncSession, payload: LoginRequest) -> TokenBundle:
    return await login_with_optional_tenant(
        db,
        tenant_id=payload.tenant_id,
        username_or_email=payload.username_or_email,
        password=payload.password,
    )


async def login_with_optional_tenant(
    db: AsyncSession,
    *,
    tenant_id: str | None,
    username_or_email: str,
    password: str,
) -> TokenBundle:
    user = await _find_user_for_login(db, tenant_id, username_or_email)
    if not user or not verify_password(password, user.password_hash):
        raise InvalidCredentialsError("Invalid username/email or password.")

    if user.email_verified_at is None:
        raise OTPVerificationError("Email is not verified. Verify OTP before login.")

    return await _issue_token_bundle(db, user=user, rotated_from_jti=None)


async def request_otp(db: AsyncSession, payload: RequestOTPRequest) -> OTPDispatchResult:
    user = await _find_user_by_identifier(db, payload.tenant_id, payload.username_or_email)
    if not user:
        raise InvalidCredentialsError("User not found.")

    return await _create_and_send_otp(
        db,
        user=user,
        channel=payload.channel,
        purpose=payload.purpose,
    )


async def verify_otp(db: AsyncSession, payload: VerifyOTPRequest) -> TokenBundle:
    user = await _find_user_by_identifier(db, payload.tenant_id, payload.username_or_email)
    if not user:
        raise InvalidCredentialsError("User not found.")

    destination = _resolve_destination(user, payload.channel)
    now = datetime.now(timezone.utc)
    challenge_query = (
        select(OTPChallenge)
        .where(
            and_(
                OTPChallenge.user_id == user.id,
                OTPChallenge.tenant_id == payload.tenant_id,
                OTPChallenge.channel == payload.channel,
                OTPChallenge.purpose == payload.purpose,
                OTPChallenge.destination == destination,
                OTPChallenge.consumed_at.is_(None),
                OTPChallenge.expires_at > now,
            )
        )
        .order_by(OTPChallenge.created_at.desc())
    )
    result = await db.execute(challenge_query)
    challenge = result.scalars().first()
    if not challenge:
        raise OTPVerificationError("OTP expired or not found.")

    if challenge.attempt_count >= challenge.max_attempts:
        raise OTPVerificationError("OTP attempts exceeded.")

    if hash_value(payload.otp_code) != challenge.code_hash:
        challenge.attempt_count += 1
        await db.commit()
        raise OTPVerificationError("Invalid OTP.")

    challenge.consumed_at = now
    if payload.channel == "email":
        user.email_verified_at = now
        user.is_verified = True
    elif payload.channel == "mobile":
        user.phone_verified_at = now

    await db.commit()
    await db.refresh(user)
    return await _issue_token_bundle(db, user=user, rotated_from_jti=None)


async def refresh_session(db: AsyncSession, refresh_token: str) -> TokenBundle:
    payload = safe_decode_token(refresh_token)
    if not payload or payload.get("token_type") != "refresh":
        raise InvalidCredentialsError("Invalid refresh token.")

    user_id = payload.get("sub")
    tenant_id = payload.get("tenant_id")
    jti = payload.get("jti")
    if not user_id or not tenant_id or not jti:
        raise InvalidCredentialsError("Invalid refresh token payload.")

    now = datetime.now(timezone.utc)
    query = select(RefreshToken).where(
        and_(
            RefreshToken.jti == jti,
            RefreshToken.user_id == user_id,
            RefreshToken.tenant_id == tenant_id,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > now,
        )
    )
    result = await db.execute(query)
    token_row = result.scalar_one_or_none()
    if not token_row or token_row.token_hash != hash_value(refresh_token):
        raise InvalidCredentialsError("Refresh token not recognized.")

    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise InvalidCredentialsError("User not found.")

    return await _issue_token_bundle(db, user=user, rotated_from_jti=jti)


async def revoke_refresh_token(db: AsyncSession, refresh_token: str) -> None:
    payload = safe_decode_token(refresh_token)
    if not payload or payload.get("token_type") != "refresh":
        return

    jti = payload.get("jti")
    user_id = payload.get("sub")
    tenant_id = payload.get("tenant_id")
    if not all([jti, user_id, tenant_id]):
        return

    query = select(RefreshToken).where(
        and_(
            RefreshToken.jti == jti,
            RefreshToken.user_id == user_id,
            RefreshToken.tenant_id == tenant_id,
            RefreshToken.revoked_at.is_(None),
        )
    )
    result = await db.execute(query)
    token_row = result.scalar_one_or_none()
    if token_row:
        token_row.revoked_at = datetime.now(timezone.utc)
        await db.commit()


async def _find_user_by_identifier(db: AsyncSession, tenant_id: str, identifier: str) -> User | None:
    query = select(User).where(
        and_(
            User.tenant_id == tenant_id,
            or_(User.username == identifier, User.email == identifier),
        )
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def _find_user_for_login(
    db: AsyncSession,
    tenant_id: str | None,
    identifier: str,
) -> User | None:
    if tenant_id:
        return await _find_user_by_identifier(db, tenant_id, identifier)

    query = select(User).where(or_(User.username == identifier, User.email == identifier)).limit(2)
    result = await db.execute(query)
    users = list(result.scalars().all())
    if not users:
        return None
    if len(users) > 1:
        raise InvalidCredentialsError(
            "Multiple tenant accounts found. Use 'tenant_id|username_or_email' in form login."
        )
    return users[0]


async def _create_and_send_otp(
    db: AsyncSession,
    *,
    user: User,
    channel: str,
    purpose: str,
) -> OTPDispatchResult:
    settings = get_settings()
    destination = _resolve_destination(user, channel)
    otp_code = generate_otp(settings.otp_length)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.otp_exp_minutes)
    challenge = OTPChallenge(
        user_id=user.id,
        tenant_id=user.tenant_id,
        channel=channel,
        purpose=purpose,
        destination=destination,
        code_hash=hash_value(otp_code),
        expires_at=expires_at,
        metadata_json={},
    )
    db.add(challenge)
    await db.flush()

    try:
        await send_otp_code(
            channel=channel,
            destination=destination,
            otp_code=otp_code,
            purpose=purpose,
        )
    except InfraOTPDispatchError as exc:
        await db.rollback()
        raise OTPDeliveryError(str(exc)) from exc

    await db.commit()
    await db.refresh(challenge)
    return OTPDispatchResult(
        expires_at=challenge.expires_at,
        channel=challenge.channel,
        destination=challenge.destination,
    )


def _resolve_destination(user: User, channel: str) -> str:
    if channel == "email":
        return user.email
    if channel == "mobile":
        if not user.phone_e164:
            raise OTPVerificationError("User does not have a verified mobile number configured.")
        return user.phone_e164
    raise OTPVerificationError(f"Unsupported OTP channel: {channel}")


async def _issue_token_bundle(
    db: AsyncSession,
    *,
    user: User,
    rotated_from_jti: str | None,
) -> TokenBundle:
    access = create_access_token(user.id, user.tenant_id)
    refresh = create_refresh_token(user.id, user.tenant_id)

    if rotated_from_jti:
        old_result = await db.execute(select(RefreshToken).where(RefreshToken.jti == rotated_from_jti))
        old_row = old_result.scalar_one_or_none()
        if old_row and old_row.revoked_at is None:
            old_row.revoked_at = datetime.now(timezone.utc)
            old_row.replaced_by_jti = refresh.jti

    refresh_row = RefreshToken(
        jti=refresh.jti,
        user_id=user.id,
        tenant_id=user.tenant_id,
        token_hash=hash_value(refresh.token),
        expires_at=refresh.expires_at,
    )
    db.add(refresh_row)
    await db.commit()

    return TokenBundle(
        access_token=access.token,
        access_expires_in=access.expires_in_seconds,
        refresh_token=refresh.token,
        refresh_expires_at=refresh.expires_at,
    )
