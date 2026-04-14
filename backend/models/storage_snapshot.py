from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class StorageSnapshot(Base):
    """Daily storage measurement per ownership group."""

    __tablename__ = "storage_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "ownership_group_id", "snapshot_date",
            name="uq_storage_snapshot_group_date",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    ownership_group_id: Mapped[str] = mapped_column(
        String(8),
        ForeignKey("ownership_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    measured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    storage_gb: Mapped[float] = mapped_column(Float, nullable=False)
    charged_usd: Mapped[float] = mapped_column(Float, nullable=False)
