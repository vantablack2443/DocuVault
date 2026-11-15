from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request. Services commit explicitly; anything else is rolled back."""
    async with request.app.state.session_factory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
