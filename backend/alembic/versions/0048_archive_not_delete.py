"""Archive instead of delete (#126).

archived_at on employees/locations/roles, erased_at on employees, name
snapshots on time_entries and payroll_exports, and ondelete rules on the
foreign keys whose referenced row can genuinely go.

Revision ID: 0048
Revises: 0047
Create Date: 2026-10-16 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None

# (table, column, referenced table, ondelete, nullable after upgrade)
_FKS = [
    ("employee_check_ins", "shift_id", "shifts", "SET NULL", True),
    ("time_entries", "check_in_id", "employee_check_ins", "SET NULL", True),
    ("time_entries", "payroll_export_id", "payroll_exports", "SET NULL", True),
    ("time_entries", "approved_by_user_id", "users", "SET NULL", True),
    ("time_entries", "attested_by_user_id", "users", "SET NULL", True),
    ("payroll_exports", "exported_by_user_id", "users", "SET NULL", True),
    ("employees", "user_id", "users", "SET NULL", True),
    ("manager_invites", "invited_by_user_id", "users", "SET NULL", True),
    ("feedback_submissions", "user_id", "users", "SET NULL", True),
    ("gdpr_export_log", "user_id", "users", "SET NULL", True),
    ("activation_events", "user_id", "users", "SET NULL", True),
    ("schedule_locks", "locked_by_user_id", "users", "CASCADE", False),
    ("password_reset_tokens", "user_id", "users", "CASCADE", False),
    ("email_verification_tokens", "user_id", "users", "CASCADE", False),
    ("user_consents", "user_id", "users", "CASCADE", False),
]
# Columns that were NOT NULL before this migration.
_WAS_NOT_NULL = {
    ("payroll_exports", "exported_by_user_id"),
    ("manager_invites", "invited_by_user_id"),
    ("feedback_submissions", "user_id"),
    ("gdpr_export_log", "user_id"),
}


def _fk_name(table: str, column: str) -> str:
    """Existing constraint names vary (Postgres defaults, and the 8-char id
    migration recreated some), so look the name up instead of guessing."""
    for fk in sa.inspect(op.get_bind()).get_foreign_keys(table):
        if fk["constrained_columns"] == [column]:
            return fk["name"]
    raise RuntimeError(f"no foreign key on {table}.{column}")


def _swap_fk(table: str, column: str, ref: str, ondelete: str | None) -> None:
    op.drop_constraint(_fk_name(table, column), table, type_="foreignkey")
    op.create_foreign_key(f"{table}_{column}_fkey", table, ref, [column], ["id"], ondelete=ondelete)


def upgrade() -> None:
    for table in ("employees", "locations", "roles"):
        op.add_column(table, sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("employees", sa.Column("erased_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("time_entries", sa.Column("employee_name", sa.String(), nullable=True))
    op.add_column("time_entries", sa.Column("location_name", sa.String(), nullable=True))
    op.add_column("time_entries", sa.Column("location_timezone", sa.String(64), nullable=True))
    op.execute(
        "UPDATE time_entries te SET employee_name = e.full_name "
        "FROM employees e WHERE e.id = te.employee_id"
    )
    op.execute(
        "UPDATE time_entries te SET location_name = l.name, location_timezone = l.timezone "
        "FROM locations l WHERE l.id = te.location_id"
    )
    for column in ("employee_name", "location_name", "location_timezone"):
        op.alter_column("time_entries", column, nullable=False)

    op.add_column("payroll_exports", sa.Column("location_name", sa.String(), nullable=True))
    op.execute(
        "UPDATE payroll_exports p SET location_name = l.name "
        "FROM locations l WHERE l.id = p.location_id"
    )

    for table, column, ref, ondelete, nullable in _FKS:
        if (table, column) in _WAS_NOT_NULL:
            op.alter_column(table, column, existing_type=sa.String(8), nullable=True)
        _swap_fk(table, column, ref, ondelete)

    # Erasing the attesting manager nulls attested_by_user_id (SET NULL above);
    # the old check demanded it be present and would block that deletion.
    # attested_at stays required: the attestation itself is still on record.
    op.drop_constraint("time_entries_attested_check", "time_entries", type_="check")
    op.create_check_constraint(
        "time_entries_attested_check", "time_entries",
        "source <> 'manager_attested' OR attested_at IS NOT NULL",
    )


def downgrade() -> None:
    nulls = op.get_bind().execute(sa.text(
        "SELECT (SELECT count(*) FROM payroll_exports WHERE exported_by_user_id IS NULL)"
        " + (SELECT count(*) FROM manager_invites WHERE invited_by_user_id IS NULL)"
        " + (SELECT count(*) FROM feedback_submissions WHERE user_id IS NULL)"
        " + (SELECT count(*) FROM gdpr_export_log WHERE user_id IS NULL)"
        " + (SELECT count(*) FROM time_entries WHERE source = 'manager_attested'"
        "    AND attested_by_user_id IS NULL)"
    )).scalar()
    if nulls:
        raise RuntimeError(
            f"{nulls} row(s) lost their user to a deletion; they cannot be made "
            "NOT NULL again. Downgrading past 0048 is refused."
        )
    archived = op.get_bind().execute(sa.text(
        "SELECT (SELECT count(*) FROM employees WHERE archived_at IS NOT NULL)"
        " + (SELECT count(*) FROM locations WHERE archived_at IS NOT NULL)"
        " + (SELECT count(*) FROM roles WHERE archived_at IS NOT NULL)"
    )).scalar()
    if archived:
        raise RuntimeError(
            f"{archived} archived row(s) exist; before 0048 they would show as active. "
            "Downgrading past 0048 is refused."
        )
    op.drop_constraint("time_entries_attested_check", "time_entries", type_="check")
    op.create_check_constraint(
        "time_entries_attested_check", "time_entries",
        "source <> 'manager_attested' OR "
        "(attested_by_user_id IS NOT NULL AND attested_at IS NOT NULL)",
    )
    for table, column, ref, _ondelete, _nullable in _FKS:
        _swap_fk(table, column, ref, None)
        if (table, column) in _WAS_NOT_NULL:
            op.alter_column(table, column, existing_type=sa.String(8), nullable=False)
    op.drop_column("payroll_exports", "location_name")
    for column in ("location_timezone", "location_name", "employee_name"):
        op.drop_column("time_entries", column)
    op.drop_column("employees", "erased_at")
    for table in ("roles", "locations", "employees"):
        op.drop_column(table, "archived_at")
