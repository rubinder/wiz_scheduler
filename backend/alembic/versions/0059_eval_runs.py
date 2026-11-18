"""Evaluation runs and per-scenario results for the AI host (#259).

Revision ID: 0059
Revises: 0058

Not tenant tables: no company_id, because eval data is synthetic and run in
isolated databases or schemas. They still carry RLS (test_migrations requires
it of every table) with an app_role policy that matches no row and no grant
to app_role, so request traffic can never read them; only the owner role
(the eval runner) and admin_role can.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None

TABLES = ["eval_runs", "eval_results"]


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("git_sha", sa.String(), nullable=False),
        sa.Column("agent_model", sa.String(), nullable=False),
        sa.Column("diner_model", sa.String(), nullable=False),
        sa.Column("prompt_hash_web", sa.String(64), nullable=False),
        sa.Column("prompt_hash_voice", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("repeat", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("notes", sa.String(), nullable=True),
        sa.CheckConstraint("mode IN ('live', 'replay')", name="ck_eval_runs_mode"),
    )
    op.create_table(
        "eval_results",
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column("run_id", sa.String(8),
                  sa.ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scenario_id", sa.String(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("failures", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("metrics", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("conversation_snapshot", JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.UniqueConstraint("run_id", "scenario_id", "attempt",
                            name="uq_eval_results_run_scenario_attempt"),
    )
    op.create_index("ix_eval_results_run_id", "eval_results", ["run_id"])

    for t in TABLES:
        op.execute(f"GRANT SELECT ON {t} TO analytics_role;")
        op.execute(f"GRANT SELECT, UPDATE ON {t} TO admin_role;")
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"DROP POLICY IF EXISTS app_no_access_{t} ON {t};")
        op.execute(f"CREATE POLICY app_no_access_{t} ON {t} FOR ALL TO app_role "
                   "USING (false) WITH CHECK (false);")
        op.execute(f"DROP POLICY IF EXISTS analytics_read_all_{t} ON {t};")
        op.execute(f"CREATE POLICY analytics_read_all_{t} ON {t} FOR SELECT "
                   "TO analytics_role USING (true);")
        op.execute(f"DROP POLICY IF EXISTS admin_access_{t} ON {t};")
        op.execute(f"CREATE POLICY admin_access_{t} ON {t} FOR ALL TO admin_role "
                   "USING (true) WITH CHECK (true);")


def downgrade() -> None:
    op.drop_table("eval_results")
    op.drop_table("eval_runs")
