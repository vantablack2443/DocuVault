from fastapi import APIRouter, Request, status

from app.auth import service
from app.auth.dependencies import PrincipalDep
from app.auth.schemas import (
    CurrentUser,
    ErrorResponse,
    LoginRequest,
    RefreshRequest,
    TokenResponse,
)
from app.auth.service import IssuedTokens
from app.core.config import SettingsDep
from app.core.network import client_ip
from app.db.session import SessionDep

router = APIRouter(
    prefix="/auth",
    tags=["auth"],
    responses={status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse}},
)


def _token_response(tokens: IssuedTokens) -> TokenResponse:
    return TokenResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=tokens.expires_in,
    )


@router.post("/login", response_model=TokenResponse, summary="Sign in with e-mail and password")
async def login(body: LoginRequest, session: SessionDep, settings: SettingsDep) -> TokenResponse:
    tokens = await service.login(session, settings, email=body.email, password=body.password)
    return _token_response(tokens)


@router.post("/refresh", response_model=TokenResponse, summary="Rotate a refresh token")
async def refresh(
    body: RefreshRequest, session: SessionDep, settings: SettingsDep
) -> TokenResponse:
    tokens = await service.refresh(session, settings, refresh_token=body.refresh_token)
    return _token_response(tokens)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Revoke a refresh token")
async def logout(body: RefreshRequest, session: SessionDep) -> None:
    await service.logout(session, refresh_token=body.refresh_token)


@router.post(
    "/guest",
    response_model=TokenResponse,
    summary="Try the demo without signing up",
    responses={status.HTTP_429_TOO_MANY_REQUESTS: {"model": ErrorResponse}},
)
async def guest(request: Request, session: SessionDep, settings: SettingsDep) -> TokenResponse:
    """Creates an isolated sandbox tenant that deletes itself after it expires."""
    tokens = await service.guest_login(
        session,
        request.app.state.redis,
        settings,
        client_ip=client_ip(
            request,
            settings.trusted_proxy_hops,
            settings.proxy_secret.get_secret_value() if settings.proxy_secret else None,
        ),
    )
    return _token_response(tokens)


@router.get("/me", response_model=CurrentUser, summary="Who am I")
async def me(principal: PrincipalDep) -> CurrentUser:
    return CurrentUser(
        id=principal.user_id,
        tenant_id=principal.tenant_id,
        email=principal.email,
        role=principal.role,
        is_guest=principal.is_guest,
    )
