"""Reservations (#254): floor plan, rules, bookings and table assignments.

reservation_tables carries an exclusion constraint so two active rows cannot
hold the same table over overlapping time; that needs btree_gist for the
text equality in a GiST index. Every table has a company_id, so each gets the
RLS block 0044/0053 apply.

Revision ID: 0057
Revises: 0056
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ExcludeConstraint, JSONB, TSTZRANGE

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None

TABLES = [
    "floor_sections",
    "dining_tables",
    "table_combinations",
    "reservation_rules",
    "reservations",
    "reservation_tables",
]

STATUSES = ("held", "pending_approval", "booked", "seated", "completed",
            "no_show", "cancelled", "expired", "declined")


def _company() -> sa.Column:
    return sa.Column("company_id", sa.String(8), sa.ForeignKey("companies.id"),
                     nullable=False)


def _location(**kw) -> sa.Column:
    return sa.Column("location_id", sa.String(8), sa.ForeignKey("locations.id"),
                     nullable=False, **kw)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "floor_sections",
        sa.Column("id", sa.String(8), primary_key=True),
        _company(), _location(),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_index("ix_floor_sections_company_id", "floor_sections", ["company_id"])
    op.create_index("ix_floor_sections_location_id", "floor_sections", ["location_id"])

    op.create_table(
        "dining_tables",
        sa.Column("id", sa.String(8), primary_key=True),
        _company(), _location(),
        sa.Column("section_id", sa.String(8),
                  sa.ForeignKey("floor_sections.id", ondelete="SET NULL"), nullable=True),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("min_seats", sa.Integer(), nullable=False),
        sa.Column("max_seats", sa.Integer(), nullable=False),
        sa.Column("combinable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("x", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("y", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("w", sa.Float(), nullable=False, server_default=sa.text("1")),
        sa.Column("h", sa.Float(), nullable=False, server_default=sa.text("1")),
        sa.Column("rotation", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_dining_tables_company_id", "dining_tables", ["company_id"])
    op.create_index("ix_dining_tables_location_id", "dining_tables", ["location_id"])
    op.create_index("uq_dining_tables_location_label", "dining_tables",
                    ["location_id", "label"], unique=True,
                    postgresql_where=sa.text("archived_at IS NULL"))

    op.create_table(
        "table_combinations",
        sa.Column("id", sa.String(8), primary_key=True),
        _company(), _location(),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("table_ids", JSONB(), nullable=False),
        sa.Column("min_seats", sa.Integer(), nullable=False),
        sa.Column("max_seats", sa.Integer(), nullable=False),
    )
    op.create_index("ix_table_combinations_company_id", "table_combinations", ["company_id"])
    op.create_index("ix_table_combinations_location_id", "table_combinations", ["location_id"])

    op.create_table(
        "reservation_rules",
        sa.Column("id", sa.String(8), primary_key=True),
        _company(), _location(unique=True),
        sa.Column("rules", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_reservation_rules_company_id", "reservation_rules", ["company_id"])

    op.create_table(
        "reservations",
        sa.Column("id", sa.String(8), primary_key=True),
        _company(), _location(),
        sa.Column("party_size", sa.Integer(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("held_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("diner_name", sa.String(), nullable=True),
        sa.Column("diner_phone", sa.String(), nullable=True),
        sa.Column("diner_email", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("confirmation_code", sa.String(), nullable=True),
        sa.Column("provider", sa.String(), nullable=False,
                  server_default=sa.text("'wizscheduler'")),
        sa.Column("external_id", sa.String(), nullable=True),
        sa.Column("conversation_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("party_size >= 1", name="ck_reservations_party_size"),
        sa.CheckConstraint("end_at > start_at", name="ck_reservations_times"),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in STATUSES) + ")",
            name="ck_reservations_status"),
    )
    op.create_index("ix_reservations_company_id", "reservations", ["company_id"])
    op.create_index("ix_reservations_company_location_start", "reservations",
                    ["company_id", "location_id", "start_at"])
    op.create_index("uq_reservations_location_code", "reservations",
                    ["location_id", "confirmation_code"], unique=True,
                    postgresql_where=sa.text("confirmation_code IS NOT NULL"))

    op.create_table(
        "reservation_tables",
        sa.Column("id", sa.String(8), primary_key=True),
        _company(),
        sa.Column("reservation_id", sa.String(8),
                  sa.ForeignKey("reservations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("table_id", sa.String(8),
                  sa.ForeignKey("dining_tables.id"), nullable=False),
        sa.Column("during", TSTZRANGE(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        ExcludeConstraint(("table_id", "="), ("during", "&&"),
                          where=sa.text("active"), using="gist",
                          name="ex_reservation_tables_no_overlap"),
    )
    op.create_index("ix_reservation_tables_company_id", "reservation_tables", ["company_id"])
    op.create_index("ix_reservation_tables_reservation_id", "reservation_tables",
                    ["reservation_id"])

    for t in TABLES:
        _rls(t)


def _rls(T: str) -> None:
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
    # Children first. The btree_gist extension stays: other work may use it.
    for t in reversed(TABLES):
        op.drop_table(t)
