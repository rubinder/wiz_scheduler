"""Employee preference submissions (#189).

Adds the per-company on/off switch (with who/when toggled) and the
employee_preference_submissions table. The table has a company_id, so it
gets the same RLS block 0044 applied to every other tenant table
(tests/conftest.py RLS_TEST mode picks it up from information_schema).

Revision ID: 0053
Revises: 0052
"""
from alembic import op
import sqlalchemy as sa

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None

T = "employee_preference_submissions"


def upgrade() -> None:
    op.add_column("companies", sa.Column(
        "employee_preference_submissions_enabled", sa.Boolean(),
        nullable=False, server_default=sa.text("false")))
    op.add_column("companies", sa.Column(
        "employee_preference_submissions_toggled_at",
        sa.DateTime(timezone=True), nullable=True))
    op.add_column("companies", sa.Column(
        "employee_preference_submissions_toggled_by", sa.String(8), nullable=True))
    op.create_foreign_key(
        "fk_companies_pref_submissions_toggled_by", "companies", "users",
        ["employee_preference_submissions_toggled_by"], ["id"], ondelete="SET NULL")

    op.create_table(
        T,
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column("company_id", sa.String(8), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("employee_id", sa.String(8),
                  sa.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("submitted_payload", sa.JSON(), nullable=False),
        sa.Column("approved_payload", sa.JSON(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.String(8),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("denial_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'denied', 'superseded')",
            name="ck_pref_submission_status"),
    )
    op.create_index(f"ix_{T}_company_id", T, ["company_id"])
    op.create_index(f"ix_{T}_employee_id", T, ["employee_id"])
    op.create_index(
        "uq_pref_submission_one_pending", T, ["employee_id"], unique=True,
        postgresql_where=sa.text("status = 'pending'"))

    # RLS, copied from 0044.
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {T} TO app_role;")
    op.execute(f"GRANT SELECT ON {T} TO analytics_role;")
    op.execute(f"GRANT SELECT, UPDATE ON {T} TO admin_role;")
    op.execute(f"ALTER TABLE {T} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"DROP POLICY IF EXISTS app_tenant_isolation_{T} ON {T};")
    op.execute(f"""
        CREATE POLICY app_tenant_isolation_{T}
            ON {T}
            FOR ALL
            TO app_role
            USING (
                company_id = ANY(
                    current_setting('app.company_ids', true)::text[]
                )
            )
            WITH CHECK (
                company_id = ANY(
                    current_setting('app.company_ids', true)::text[]
                )
            );
    """)
    op.execute(f"DROP POLICY IF EXISTS analytics_read_all_{T} ON {T};")
    op.execute(f"""
        CREATE POLICY analytics_read_all_{T}
            ON {T}
            FOR SELECT
            TO analytics_role
            USING (true);
    """)
    op.execute(f"DROP POLICY IF EXISTS admin_access_{T} ON {T};")
    op.execute(f"""
        CREATE POLICY admin_access_{T}
            ON {T}
            FOR ALL
            TO admin_role
            USING (true)
            WITH CHECK (true);
    """)


def downgrade() -> None:
    op.drop_table(T)  # drops its indexes and policies with it
    op.drop_constraint(
        "fk_companies_pref_submissions_toggled_by", "companies", type_="foreignkey")
    op.drop_column("companies", "employee_preference_submissions_toggled_by")
    op.drop_column("companies", "employee_preference_submissions_toggled_at")
    op.drop_column("companies", "employee_preference_submissions_enabled")
