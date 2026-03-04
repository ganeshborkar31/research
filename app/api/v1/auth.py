from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.schemas.auth_api import (
    AuthTokenResponse,
    LoginRequest,
    LogoutResponse,
    OTPRequestedResponse,
    RefreshRequest,
    RequestOTPRequest,
    SignupRequest,
    VerifyOTPRequest,
)
from app.services.auth_service import (
    AuthError,
    InvalidCredentialsError,
    OTPVerificationError,
    login,
    login_with_optional_tenant,
    refresh_session,
    request_otp,
    revoke_refresh_token,
    signup,
    verify_otp,
)


router = APIRouter()


@router.post("/signup", response_model=OTPRequestedResponse, status_code=status.HTTP_201_CREATED)
async def signup_user(payload: SignupRequest, db: AsyncSession = Depends(get_db)) -> OTPRequestedResponse:
    try:
        _user, otp_result = await signup(db, payload)
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return OTPRequestedResponse(
        expires_at=otp_result.expires_at,
        channel=otp_result.channel,
        destination=otp_result.destination,
    )


@router.post("/request-otp", response_model=OTPRequestedResponse)
async def request_otp_code(
    payload: RequestOTPRequest,
    db: AsyncSession = Depends(get_db),
) -> OTPRequestedResponse:
    try:
        otp_result = await request_otp(db, payload)
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return OTPRequestedResponse(
        expires_at=otp_result.expires_at,
        channel=otp_result.channel,
        destination=otp_result.destination,
    )


@router.post("/verify-otp", response_model=AuthTokenResponse)
async def verify_otp_code(
    payload: VerifyOTPRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> AuthTokenResponse:
    try:
        token_bundle = await verify_otp(db, payload)
    except OTPVerificationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    _set_refresh_cookie(response, token_bundle.refresh_token)
    return AuthTokenResponse(
        access_token=token_bundle.access_token,
        expires_in=token_bundle.access_expires_in,
    )


@router.post("/login", response_model=AuthTokenResponse)
async def login_user(
    payload: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> AuthTokenResponse:
    try:
        token_bundle = await login(db, payload)
    except OTPVerificationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    _set_refresh_cookie(response, token_bundle.refresh_token)
    return AuthTokenResponse(
        access_token=token_bundle.access_token,
        expires_in=token_bundle.access_expires_in,
    )


@router.post("/login/form", response_model=AuthTokenResponse)
async def login_user_form(
    response: Response,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: AsyncSession = Depends(get_db),
) -> AuthTokenResponse:
    tenant_id, username_or_email = _parse_compound_username(form_data.username)
    try:
        token_bundle = await login_with_optional_tenant(
            db,
            tenant_id=tenant_id,
            username_or_email=username_or_email,
            password=form_data.password,
        )
    except OTPVerificationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    _set_refresh_cookie(response, token_bundle.refresh_token)
    return AuthTokenResponse(
        access_token=token_bundle.access_token,
        expires_in=token_bundle.access_expires_in,
    )


@router.post("/refresh", response_model=AuthTokenResponse)
async def refresh_tokens(
    payload: RefreshRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> AuthTokenResponse:
    refresh_token = _resolve_refresh_token(payload, request)
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token not provided.",
        )

    try:
        token_bundle = await refresh_session(db, refresh_token)
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    _set_refresh_cookie(response, token_bundle.refresh_token)
    return AuthTokenResponse(
        access_token=token_bundle.access_token,
        expires_in=token_bundle.access_expires_in,
    )


@router.post("/logout", response_model=LogoutResponse)
async def logout_user(
    payload: RefreshRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> LogoutResponse:
    refresh_token = _resolve_refresh_token(payload, request)
    if refresh_token:
        await revoke_refresh_token(db, refresh_token)
    _clear_refresh_cookie(response)
    return LogoutResponse()


def _resolve_refresh_token(payload: RefreshRequest, request: Request) -> str | None:
    if payload.refresh_token:
        return payload.refresh_token
    settings = get_settings()
    return request.cookies.get(settings.refresh_cookie_name)


def _parse_compound_username(compound_username: str) -> tuple[str | None, str]:
    # OAuth2 form has only username/password, so tenant and user are split by "|".
    value = compound_username.strip()
    if not value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username cannot be empty.",
        )
    if "|" not in value:
        return None, value
    tenant_id, username_or_email = value.split("|", 1)
    tenant_id = tenant_id.strip() or None
    username_or_email = username_or_email.strip()
    if not username_or_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid form username format. Expected 'tenant_id|username_or_email'.",
        )
    return tenant_id, username_or_email


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    settings = get_settings()
    max_age = settings.refresh_token_exp_days * 24 * 60 * 60
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=refresh_token,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
        max_age=max_age,
        path="/",
    )


def _clear_refresh_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path="/",
    )
