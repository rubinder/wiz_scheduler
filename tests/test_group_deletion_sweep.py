"""Every company-keyed table is handled by billing.delete_og_and_dependents.

A table with a company_id column whose rows outlive the group either blocks
the deletion (its FK to companies.id has no ON DELETE, so the companies
delete raises) or keeps company data we promised to delete. A table counts
as handled when the function deletes its model by name, or when one of its
foreign keys cascades from a table that is handled.
"""

import inspect
import re

from sqlalchemy import Table

import backend.main  # noqa: F401  (registers every model on Base.metadata)
from backend.database import Base
from backend.services import billing

# Company-keyed tables deliberately left in place, with the reason. Pay
# records are NOT here: time_entries and payroll_exports are deleted with the
# group; what outlives it is payroll_archives (#218), which has no company_id
# (and no FK to ownership_groups) so it never shows up in this sweep.
KEPT: dict[str, str] = {}


def _models_by_table() -> dict[str, str]:
    return {m.local_table.name: m.class_.__name__ for m in Base.registry.mappers
            if isinstance(m.local_table, Table)}


def _handled_tables() -> set[str]:
    src = inspect.getsource(billing.delete_og_and_dependents)
    names = _models_by_table()
    handled = {t for t, cls in names.items() if re.search(rf"\b{cls}\b", src)}
    # Cascades: a row goes with a parent row that is itself deleted.
    changed = True
    while changed:
        changed = False
        for table in Base.metadata.tables.values():
            if table.name in handled:
                continue
            if any(fk.ondelete and fk.ondelete.upper() == "CASCADE"
                   and fk.column.table.name in handled
                   for c in table.c for fk in c.foreign_keys):
                handled.add(table.name)
                changed = True
    return handled


def test_every_company_keyed_table_is_deleted_with_the_group() -> None:
    company_keyed = {t.name for t in Base.metadata.tables.values() if "company_id" in t.c}
    missed = sorted(company_keyed - _handled_tables() - set(KEPT))
    assert missed == [], f"not deleted by delete_og_and_dependents: {missed}"


def test_kept_tables_still_exist() -> None:
    assert set(KEPT) <= set(Base.metadata.tables)
