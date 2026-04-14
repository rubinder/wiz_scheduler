"""Dev-only: add a manager user bypassing Stripe signup.

Usage:
    cd backend && python add_dev_user.py
"""

import asyncio

from passlib.context import CryptContext
from sqlalchemy import text

from backend.database import async_session_factory

EMAIL = "rubinder_4@hotmail.com"
PASSWORD = "password"
FULL_NAME = "Rubinder"
COMPANY_NAME = "Rubinder's Company"

OWNERSHIP_GROUP_ID = "rbowngp1"
COMPANY_ID = "rbcomp01"
USER_ID = "rbuser01"
COMPANY_SLUG = "rubinder01"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


async def main() -> None:
    hashed_password = pwd_context.hash(PASSWORD)

    async with async_session_factory() as db:
        # Ownership group
        await db.execute(
            text(
                "INSERT INTO ownership_groups (id, name) "
                "VALUES (:id, :name) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": OWNERSHIP_GROUP_ID, "name": COMPANY_NAME},
        )

        # Company
        await db.execute(
            text(
                "INSERT INTO companies (id, ownership_group_id, name, slug) "
                "VALUES (:id, :og_id, :name, :slug) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": COMPANY_ID,
                "og_id": OWNERSHIP_GROUP_ID,
                "name": COMPANY_NAME,
                "slug": COMPANY_SLUG,
            },
        )

        # User (manager) — update password if user already exists
        await db.execute(
            text(
                "INSERT INTO users (id, company_id, email, hashed_password, full_name, user_role) "
                "VALUES (:id, :company_id, :email, :pw, :name, 'manager') "
                "ON CONFLICT (id) DO UPDATE SET hashed_password = EXCLUDED.hashed_password"
            ),
            {
                "id": USER_ID,
                "company_id": COMPANY_ID,
                "email": EMAIL,
                "pw": hashed_password,
                "name": FULL_NAME,
            },
        )

        await db.commit()
        print(f"User '{EMAIL}' ready. Log in with password: {PASSWORD}")


if __name__ == "__main__":
    asyncio.run(main())
