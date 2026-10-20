"""Every foreign key to users.id says what happens when the user goes (#126).

GDPR deletion removes the `users` row. A foreign key with no ondelete turns
that into a 500 for any manager who ever approved, exported, invited or
reset a password.
"""

from backend.database import Base
import backend.models  # noqa: F401  (registers every table)


def test_every_users_fk_declares_ondelete():
    missing = []
    for table in Base.metadata.tables.values():
        for fk in table.foreign_keys:
            if fk.column.table.name == "users" and fk.ondelete not in ("CASCADE", "SET NULL"):
                missing.append(f"{table.name}.{fk.parent.name}")
    assert missing == []


def test_set_null_columns_are_nullable():
    bad = []
    for table in Base.metadata.tables.values():
        for fk in table.foreign_keys:
            if fk.ondelete == "SET NULL" and not fk.parent.nullable:
                bad.append(f"{table.name}.{fk.parent.name}")
    assert bad == []
