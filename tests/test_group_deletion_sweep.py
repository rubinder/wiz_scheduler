"""billing.delete_og_and_dependents deletes everything that would block it.

A table counts as deleted only when the function names its model as the
target of a ``delete(Model)`` call (directly, or through a
``for model in (...): delete(model)`` loop), plus ``db.delete(og)`` for the
group row itself. Mentioning a model anywhere else (an import, a comment, a
``.where`` on another table) does not count. Tables whose FK cascades from a
deleted table are deleted too.

Two sweeps run over Base.metadata:

1. Every table with a NO ACTION / RESTRICT foreign key into a deleted table
   must itself be deleted, or the delete of its parent raises and the whole
   day-90 deletion rolls back (it did, for every group, while
   activation_events was missed). SET NULL foreign keys need nothing.
2. Every company-keyed table is deleted, even without a foreign key, so no
   company data outlives the group.
"""

import ast
import inspect
import textwrap
from typing import Any, Callable

from sqlalchemy import Table

import backend.main  # noqa: F401  (registers every model on Base.metadata)
from backend.database import Base
from backend.services import billing

# Tables deliberately left in place although they reference a deleted table
# or carry company_id, with the reason. Pay records are NOT here:
# time_entries and payroll_exports are deleted with the group; what outlives
# it is payroll_archives (#218), which has no company_id and no FK to
# ownership_groups, so it never shows up in either sweep.
KEPT: dict[str, str] = {}

_BLOCKING = {None, "NO ACTION", "RESTRICT"}


def _models_by_class() -> dict[str, str]:
    return {m.class_.__name__: m.local_table.name for m in Base.registry.mappers
            if isinstance(m.local_table, Table)}


def _delete_targets(fn: Callable[..., Any] = billing.delete_og_and_dependents) -> set[str]:
    """Class names passed as the first argument of a delete(...) call."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    loop_models: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.For) and isinstance(node.target, ast.Name)
                and isinstance(node.iter, (ast.Tuple, ast.List))):
            loop_models.setdefault(node.target.id, set()).update(
                e.id for e in node.iter.elts if isinstance(e, ast.Name))
    targets: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and node.args):
            continue
        arg = node.args[0]
        if not isinstance(arg, ast.Name):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "delete":
            targets |= loop_models.get(arg.id, {arg.id})
        elif (isinstance(node.func, ast.Attribute) and node.func.attr == "delete"
              and arg.id == "og"):
            targets.add("OwnershipGroup")
    return targets


def _deleted_tables() -> set[str]:
    by_class = _models_by_class()
    deleted = {by_class[c] for c in _delete_targets() if c in by_class}
    # Cascades: a row goes with a parent row that is itself deleted.
    changed = True
    while changed:
        changed = False
        for table in Base.metadata.tables.values():
            if table.name in deleted:
                continue
            if any(fk.ondelete and fk.ondelete.upper() == "CASCADE"
                   and fk.column.table.name in deleted
                   for c in table.c for fk in c.foreign_keys):
                deleted.add(table.name)
                changed = True
    return deleted


async def _sample(db: Any, og: Any) -> None:  # pragma: no cover - parsed only
    # ManagerInvite is named here, in a comment, and never deleted.
    from backend.models.manager_invite import ManagerInvite  # noqa: F401
    await db.execute(delete(Company).where(Company.id == ManagerInvite.accepted_company_id))  # noqa: F821
    for model in (Region, Role):  # noqa: F821
        await db.execute(delete(model).where(model.company_id == "x"))
    await db.delete(og)


def test_sweep_counts_only_delete_targets() -> None:
    assert _delete_targets(_sample) == {"Company", "Region", "Role", "OwnershipGroup"}
    assert {"OwnershipGroup", "Company", "AgentDailyMetric"} <= _delete_targets()


def test_no_table_blocks_the_group_deletion() -> None:
    deleted = _deleted_tables()
    missed = sorted({
        f"{table.name}.{c.name} -> {fk.column.table.name}"
        for table in Base.metadata.tables.values()
        if table.name not in deleted and table.name not in KEPT
        for c in table.c for fk in c.foreign_keys
        if fk.column.table.name in deleted
        and (fk.ondelete.upper() if fk.ondelete else None) in _BLOCKING
    })
    assert missed == [], f"NO ACTION FKs into tables delete_og_and_dependents deletes: {missed}"


def test_every_company_keyed_table_is_deleted_with_the_group() -> None:
    company_keyed = {t.name for t in Base.metadata.tables.values() if "company_id" in t.c}
    missed = sorted(company_keyed - _deleted_tables() - set(KEPT))
    assert missed == [], f"not deleted by delete_og_and_dependents: {missed}"


def test_kept_tables_still_exist() -> None:
    assert set(KEPT) <= set(Base.metadata.tables)
