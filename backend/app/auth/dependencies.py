from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth import service
from app.auth.service import ApiKeyPrincipal, Principal
from app.core.config import SettingsDep
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.db.models import Role
from app.db.session import SessionDep

_user_bearer = HTTPBearer(
    auto_error=False, scheme_name="AccessToken", description="JWT access token"
)
_key_bearer = HTTPBearer(auto_error=False, scheme_name="ApiKey", description="`dv_…` API key")


async def get_principal(
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_user_bearer)],
) -> Principal:
    if credentials is None:
        raise AuthenticationError
    return await service.authenticate_access_token(session, settings, credentials.credentials)


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def require_roles(*allowed: Role) -> Callable[[Principal], Awaitable[Principal]]:
    """Dependency factory: `Depends(require_roles(Role.ADMIN))`."""

    async def dependency(principal: PrincipalDep) -> Principal:
        if principal.role not in allowed:
            raise PermissionDeniedError
        return principal

    return dependency


async def get_api_key_principal(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_key_bearer)],
) -> ApiKeyPrincipal:
    if credentials is None:
        raise AuthenticationError
    return await service.authenticate_api_key(session, credentials.credentials)


ApiKeyPrincipalDep = Annotated[ApiKeyPrincipal, Depends(get_api_key_principal)]
