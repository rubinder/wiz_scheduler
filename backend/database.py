from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, ORMExecuteState, Session, with_loader_criteria

from backend.config import settings
from backend.tenant import get_tenant_context

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


class Base(DeclarativeBase):
    pass
