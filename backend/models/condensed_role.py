import uuid

from sqlalchemy import ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class CondensedRole(Base):
    __tablename__ = "condensed_roles"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)

    mappings: Mapped[list["CondensedRoleMapping"]] = relationship(
        back_populates="condensed_role", lazy="selectin", cascade="all, delete-orphan"
    )


class CondensedRoleMapping(Base):
    __tablename__ = "condensed_role_mappings"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    condensed_role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("condensed_roles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True
    )

    condensed_role: Mapped["CondensedRole"] = relationship(back_populates="mappings")
