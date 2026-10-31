"""Paid payroll archives (#218).

payroll_archives records a cancelled group's one-time purchase of a
seven-year pay-record archive and, once the day-90 deletion writes it, where
the file is. payroll_archive_tokens holds the sha256 of each single-use
download link mailed to a recipient.

ownership_group_id is deliberately not a foreign key: the group is
hard-deleted at day 90 and the archive must outlive it.

RLS, the 0052 way (tests/test_migrations.py requires an app_role policy on
every table): enabled, not forced, so the owner -- which runs the
cancellation cron, the Stripe webhook and the unauthenticated retrieval
routes -- keeps full access. An authenticated request (app_role) sees only
its own group's archive, and a token only through an archive it can see.
`STATEMENTS` is also executed by tests/conftest.py in RLS_TEST mode.

Revision ID: 0056
Revises: 0055
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None

POLICIES: dict[str, str] = {
    "payroll_archives": "ownership_group_id = ANY(app_ownership_group_ids())",
    "payroll_archive_tokens": (
        "EXISTS (SELECT 1 FROM payroll_archives a WHERE a.id = archive_id)"
    ),
}

STATEMENTS: list[str] = []
for _table, _expr in POLICIES.items():
    STATEMENTS += [
        f"ALTER TABLE {_table} ENABLE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS app_tenant_isolation_{_table} ON {_table}",
        f"""
        CREATE POLICY app_tenant_isolation_{_table} ON {_table}
            FOR ALL TO app_role
            USING ({_expr})
            WITH CHECK ({_expr})
        """,
    ]


def upgrade() -> None:
    op.create_table(
        "payroll_archives",
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column("ownership_group_id", sa.String(8), nullable=False),
        sa.Column("company_names", JSONB(), nullable=False),
        sa.Column("stripe_checkout_session_id", sa.String(), nullable=False),
        sa.Column("stripe_payment_intent_id", sa.String(), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recipient_emails", JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False,
                  server_default=sa.text("'paid'")),
        sa.Column("s3_key", sa.String(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("written_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('paid', 'written')", name="payroll_archives_status_check"),
        sa.UniqueConstraint(
            "ownership_group_id", name="payroll_archives_ownership_group_id_key"),
        sa.UniqueConstraint(
            "stripe_checkout_session_id",
            name="payroll_archives_stripe_checkout_session_id_key"),
    )
    op.create_table(
        "payroll_archive_tokens",
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column("archive_id", sa.String(8),
                  sa.ForeignKey("payroll_archives.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "token_hash", name="payroll_archive_tokens_token_hash_key"),
    )
    op.create_index(
        "ix_payroll_archive_tokens_archive_id", "payroll_archive_tokens", ["archive_id"])
    op.create_index(
        "ix_payroll_archive_tokens_email", "payroll_archive_tokens", ["email"])

    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.drop_table("payroll_archive_tokens")  # its indexes and policy go with it
    op.drop_table("payroll_archives")
