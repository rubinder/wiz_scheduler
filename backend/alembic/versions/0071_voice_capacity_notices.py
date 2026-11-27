"""Voice capacity notices and stale quota requests (decision 104, #259 follow-up).

voice_capacity_notices: one row per capacity email sent, keyed (kind,
subject_id, UTC day). The daily job and the operator script claim a key with
INSERT ... ON CONFLICT DO NOTHING before sending, so several ECS tasks or
workers send each email once. Platform data like voice_quota_requests (0069):
the owner only, a no-row app_role policy, analysts may read. No company
column: a subject is an internal id, never a diner identifier or phone
number. Pruned after 30 days by the daily capacity step.

voice_quota_requests.status gains 'stale' (AWS kept the request pending past
VOICE_QUOTA_REQUEST_STALE_DAYS, so it is no longer polled) and
'pending_upstream' (AWS refused a new filing because one is already open).

Revision ID: 0071
Revises: 0070
"""
from alembic import op
import sqlalchemy as sa

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None

T = "voice_capacity_notices"
STATUSES = ("pending", "manual", "approved", "denied", "failed", "superseded")


def _status_check(statuses: tuple[str, ...]) -> None:
    op.drop_constraint("ck_voice_quota_requests_status", "voice_quota_requests", type_="check")
    op.create_check_constraint(
        "ck_voice_quota_requests_status", "voice_quota_requests",
        "status IN (" + ", ".join(f"'{s}'" for s in statuses) + ")")


def upgrade() -> None:
    op.create_table(
        T,
        sa.Column("kind", sa.String(40), primary_key=True),
        sa.Column("subject_id", sa.String(64), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON {T} FROM app_role;")
    op.execute(f"GRANT SELECT ON {T} TO analytics_role;")
    op.execute(f"ALTER TABLE {T} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"""
        CREATE POLICY app_no_rows_{T} ON {T}
            FOR SELECT TO app_role USING (false);
    """)
    op.execute(f"""
        CREATE POLICY analytics_read_all_{T} ON {T}
            FOR SELECT TO analytics_role USING (true);
    """)
    _status_check(STATUSES + ("stale", "pending_upstream"))


def downgrade() -> None:
    # Neither is a request the older code can follow: 'failed' is the
    # nearest status it knows.
    op.execute("UPDATE voice_quota_requests SET status = 'failed' "
               "WHERE status IN ('stale', 'pending_upstream')")
    _status_check(STATUSES)
    op.drop_table(T)
