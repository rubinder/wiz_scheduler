from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class CondensedRole(Base):
    __tablename__ = "condensed_roles"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)

    mappings: Mapped[list["CondensedRoleMapping"]] = relationship(
        back_populates="condensed_role", lazy="selectin", cascade="all, delete-orphan"
    )


class CondensedRoleMapping(Base):
    __tablename__ = "condensed_role_mappings"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    condensed_role_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("condensed_roles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True
    )

    condensed_role: Mapped["CondensedRole"] = relationship(back_populates="mappings")
