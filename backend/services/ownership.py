"""Check that IDs taken from a request body belong to the caller's tenant.

Nothing filters queries by company automatically, so a body that names a row
by ID (an employee's role, a shift's location, a template's location) must be
checked explicitly before it is stored. Storing another tenant's ID would
link our row to theirs: it can leak their names through our reads and block
their deletes through the foreign key.
"""
from collections.abc import Collection, Iterable
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def assert_owned(
    db: AsyncSession,
    model: Any,
    ids: Iterable[str | None],
    company_ids: Collection[str],
    what: str,
) -> None:
    """404 ``"<what> not found"`` unless every non-null ID is a ``model`` row
    whose ``company_id`` is in ``company_ids``.

    Pass the caller's own company for rows a single company owns, or its
    ownership group's companies where a row may be shared across them (an
    employee's roles and locations).
    """
    wanted = {str(i) for i in ids if i is not None}
    if not wanted:
        return
    found = set(
        (await db.execute(
            select(model.id).where(
                model.id.in_(wanted),
                model.company_id.in_([str(c) for c in company_ids]),
            )
        )).scalars().all()
    )
    if wanted - found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found")
