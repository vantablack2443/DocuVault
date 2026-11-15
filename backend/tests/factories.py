import uuid
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.config import get_settings
from app.db.models import Role, Tenant, User

PASSWORD = "correct horse battery staple"


async def create_tenant(session: AsyncSession) -> Tenant:
    tenant = Tenant(name=f"Test {uuid.uuid4().hex[:8]}")
    session.add(tenant)
    await session.commit()
    return tenant


async def create_user(
    session: AsyncSession,
    *,
    role: Role = Role.ADMIN,
    tenant: Tenant | None = None,
    with_password: bool = False,
    is_active: bool = True,
) -> User:
    tenant = tenant or await create_tenant(session)
    user = User(
        tenant_id=tenant.id,
        email=f"{role.value}-{uuid.uuid4().hex[:10]}@example.test",
        role=role,
        is_active=is_active,
        password_hash=security.hash_password(PASSWORD) if with_password else None,
    )
    session.add(user)
    await session.commit()
    return user


def auth_headers(user: User) -> dict[str, str]:
    """Mint an access token directly; skips the login round trip and Argon2."""
    token = security.create_access_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        role=user.role,
        secret=get_settings().jwt_secret.get_secret_value(),
        ttl=timedelta(minutes=5),
    )
    return {"Authorization": f"Bearer {token}"}
