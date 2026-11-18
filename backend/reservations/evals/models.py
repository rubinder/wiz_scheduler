"""Stored eval runs (#259). Metadata only (decision 36, Ruling R): there is
no migration and no production table. The runner creates these tables with
`create_all` in its scratch schema, alongside the application's, and drops
them with it; every run is also written to `evals-runs/<run-id>.json` at the
repo root before the schema goes. Not tenant tables: no company_id, because
eval data is synthetic."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class EvalRun(Base):
    __tablename__ = "eval_runs"
    __table_args__ = (CheckConstraint("mode IN ('live', 'replay')", name="ck_eval_runs_mode"),)

    id: Mapped[str] = mapped_column(String(8), primary_key=True, default=generate_short_id)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    git_sha: Mapped[str] = mapped_column(String, nullable=False)
    agent_model: Mapped[str] = mapped_column(String, nullable=False)
    diner_model: Mapped[str] = mapped_column(String, nullable=False)
    prompt_hash_web: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_hash_voice: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    repeat: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1"))
    notes: Mapped[str | None] = mapped_column(String, nullable=True)


class EvalResult(Base):
    __tablename__ = "eval_results"
    __table_args__ = (UniqueConstraint(
        "run_id", "scenario_id", "attempt", name="uq_eval_results_run_scenario_attempt"),)

    id: Mapped[str] = mapped_column(String(8), primary_key=True, default=generate_short_id)
    run_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("eval_runs.id", ondelete="CASCADE"),
        nullable=False, index=True)
    scenario_id: Mapped[str] = mapped_column(String, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    failures: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    conversation_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
