from typing import AsyncGenerator
import json

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import async_session_factory
from backend.models.company import Company
from backend.models.user import User
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
    """Set Postgres session variable for RLS policies (Part 2: defense-in-depth).

    This function sets app.company_ids as a Postgres session variable that RLS
    policies use to filter data. It's called from get_current_user after the
    tenant context is set.

    The RLS policies created by migration 0044 use:
      current_setting('app.company_ids', true)::text[]

    to filter rows. This provides a second layer of tenant isolation beyond the
    application-level filtering in Part 1.

    If RLS policies have not been applied yet (e.g., migration 0044 not run),
    this function silently succeeds with no effect — the app falls back to the
    application-level filtering only.

    Args:
        db: The AsyncSession
        company_ids: List of company IDs this session is allowed to access
    """
    try:
        # Convert company_ids list to PostgreSQL array format
        # company_ids are UUIDs from the database, but we use set_config()
        # for safety (parameterized, not string interpolation)
        company_ids_json = json.dumps(company_ids)

        # Use set_config() function (parameterized) instead of raw SET to avoid injection
        # set_config(name, value, is_local) returns the old value
        # is_local=false means the setting persists for the session (not just transaction)
        await db.execute(
            text("SELECT set_config(:name, :val, false)"),
            {"name": "app.company_ids", "val": company_ids_json}
        )
    except (ValueError, TypeError) as e:
        # Invalid company_ids format (shouldn't happen if from database)
        import logging
        log = logging.getLogger("wizscheduler.rls")
        log.error(f"Invalid company_ids for RLS context: {e}")
        raise
    except Exception as e:
        # If RLS is not set up (migration not applied), log and continue
        # The application-level filtering in Part 1 still protects the data
        import logging
        log = logging.getLogger("wizscheduler.rls")
        log.debug(f"Could not set RLS context (migration 0044 may not be applied): {e}")


async def get_ownership_group_company_ids(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[str]:
    """Return all company IDs in the current user's ownership group.

    If the user's company has no ownership group, returns only their own company_id.
    """
    return await _get_company_ids_for_user(current_user, db)
