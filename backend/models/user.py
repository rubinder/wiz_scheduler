import uuid

import sqlalchemy as sa
from sqlalchemy import ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String, nullable=True)
    user_role: Mapped[str] = mapped_column(String, nullable=False)  # 'manager' | 'employee'

    __table_args__ = (
        sa.UniqueConstraint("email", "company_id", name="uq_users_email_company"),
    )
