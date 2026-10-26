from typing import AsyncGenerator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import RLS_COMPANY_IDS, apply_rls, async_session_factory
from backend.models.company import Company
from backend.models.user import User
from backend.services.location_scope import LocationScope, is_restricted_manager, location_scope_for
from backend.tenant import set_tenant_context

security = HTTPBearer()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id: str | None = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    # Set tenant context for automatic ORM filtering (Part 1: app-level)
    company_ids = await _get_company_ids_for_user(user, db)
    set_tenant_context(company_ids)

    # Set Postgres RLS context (Part 2: database-level)
    # This tells Postgres which company_ids this session can access via RLS policies
    # Only works if RLS policies have been applied by migration 0044
    await _set_rls_context(db, company_ids)

    return user


async def require_manager(
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.user_role != "manager":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Manager access required")
    return current_user


async def require_admin(current_user: User = Depends(require_manager)) -> User:
    """Company settings, billing, team, imports and payroll (spec Q1, Q4).
    Only an explicit 'regular' manager is refused."""
    if is_restricted_manager(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


async def get_location_scope(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> LocationScope:
    """The locations this manager may act on; unrestricted for admins."""
    return await location_scope_for(db, current_user)


async def _get_company_ids_for_user(user: User, db: AsyncSession) -> list[str]:
    """Helper to get all company IDs for a user's ownership group."""
    result = await db.execute(
        select(Company.ownership_group_id).where(Company.id == user.company_id)
    )
    ownership_group_id = result.scalar_one_or_none()

    if ownership_group_id is None:
        return [user.company_id]

    result = await db.execute(
        select(Company.id).where(Company.ownership_group_id == ownership_group_id)
    )
    return list(result.scalars().all())


async def _set_rls_context(db: AsyncSession, company_ids: list[str]) -> None:
    """Bind this request's session to its companies for Postgres RLS (#183).

    Every transaction the session begins from here on runs as app_role with
    app.company_ids set (database._apply_rls_on_begin); the one already open
    -- it loaded the user -- is switched now. Off unless RLS_ENFORCE is set:
    the policies are inert for the owner role the app connects as.
    """
    db.info[RLS_COMPANY_IDS] = list(company_ids)
    if settings.RLS_ENFORCE and db.in_transaction():
        connection = await db.connection()
        await connection.run_sync(lambda sync_conn: apply_rls(sync_conn, list(company_ids)))


async def get_ownership_group_company_ids(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[str]:
    """Return all company IDs in the current user's ownership group.

    If the user's company has no ownership group, returns only their own company_id.
    """
    return await _get_company_ids_for_user(current_user, db)
