from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, ORMExecuteState, Session, with_loader_criteria

from backend.config import settings
from backend.tenant import get_tenant_context, tenant_scope_disabled

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=30,
    pool_recycle=1800,
    pool_pre_ping=True,
    # Keep bound values (emails, names, password hashes) out of exception
    # messages, which end up in logs and failure_logs.
    hide_parameters=True,
)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _tenant_models() -> list[type]:
    """Every mapped class with a company_id column."""
    return [
        mapper.class_
        for mapper in Base.registry.mappers
        if "company_id" in mapper.columns
    ]


def _add_tenant_filter(orm_execute_state: ORMExecuteState) -> None:
    """Limit every ORM SELECT to the request's companies.

    Applied with ``with_loader_criteria``, so it reaches every tenant table a
    statement touches -- joined, aliased, eagerly or lazily loaded -- not
    just its primary entity. Writes are not filtered: they act on rows a
    filtered read loaded, or carry their own company_id condition.
    """
    company_ids = get_tenant_context()

    # Skip filtering if no tenant context (public routes, webhooks, background jobs without scope)
    if company_ids is None:
        return
    if not orm_execute_state.is_select or orm_execute_state.is_column_load:
        return

    ids = list(company_ids)
    orm_execute_state.statement = orm_execute_state.statement.options(*(
        with_loader_criteria(
            model,
            lambda cls: cls.company_id.in_(ids),
            include_aliases=True,
        )
        for model in _tenant_models()
    ))


# Attach the tenant filter listener to all sessions
# do_orm_execute is a Session event, but AsyncSession uses Session internally
event.listen(Session, "do_orm_execute", _add_tenant_filter)


# Set on a session by get_current_user: the companies its request may see.
RLS_COMPANY_IDS = "rls_company_ids"


def pg_text_array(values: list[str]) -> str:
    """A Postgres array literal, the form the 0044 policies cast with ::text[]."""
    return "{" + ",".join('"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"' for v in values) + "}"


def apply_rls(connection, company_ids: list[str]) -> None:
    """Make the current transaction run as app_role, limited to company_ids.

    Both settings are LOCAL: they end with the transaction, commit or
    rollback, so a pooled connection never carries one request's tenant into
    another's, and a rollback mid-request cannot leave the rest of it
    running as the unrestricted owner -- the next transaction reapplies them.
    """
    connection.execute(text("SET LOCAL ROLE app_role"))
    connection.execute(
        text("SELECT set_config('app.company_ids', :ids, true)"),
        {"ids": pg_text_array(company_ids)},
    )


def _apply_rls_on_begin(session: Session, transaction, connection) -> None:
    company_ids = session.info.get(RLS_COMPANY_IDS)
    if company_ids is not None and settings.RLS_ENFORCE:
        apply_rls(connection, company_ids)


event.listen(Session, "after_begin", _apply_rls_on_begin)


@asynccontextmanager
async def cross_tenant_session(db: AsyncSession) -> AsyncIterator[AsyncSession]:
    """A session that sees every tenant, for the few request-time checks
    that must (is this Google account linked to someone else, anywhere?).

    A fresh session on db's engine carries no RLS binding, so it runs as the
    owner; tenant_scope_disabled lifts the ORM filter for it. Keep its use to
    reads that answer a yes/no question across tenants.
    """
    with tenant_scope_disabled():
        async with AsyncSession(db.bind, expire_on_commit=False) as session:
            yield session


class Base(DeclarativeBase):
    pass
