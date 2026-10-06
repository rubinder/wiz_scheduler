from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class ManagerLocation(Base):
    __tablename__ = "manager_locations"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    manager_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    location_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("locations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Relationships
    manager: Mapped["User"] = relationship("User", back_populates="manager_locations")
    location: Mapped["Location"] = relationship("Location")

    __table_args__ = (
        UniqueConstraint("manager_id", "location_id", name="uq_manager_locations"),
    )
