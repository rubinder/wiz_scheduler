from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session

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


def _add_tenant_filter(orm_execute_state):
    """ORM listener that adds tenant filtering to SELECT statements."""
    company_ids = get_tenant_context()

    # Skip filtering if no tenant context (public routes, webhooks, background jobs without scope)
    if company_ids is None:
        return

    # Only filter SELECT statements
    if not orm_execute_state.is_select:
        return

    statement = orm_execute_state.statement

    # For each mapped entity in the query, add company_id filter if it exists
    if hasattr(orm_execute_state, "orm_context") and orm_execute_state.orm_context:
        for mapper in orm_execute_state.orm_context.mappers:
            model = mapper.class_
            if hasattr(model, "company_id"):
                statement = statement.where(model.company_id.in_(company_ids))

    orm_execute_state.statement = statement


# Attach the tenant filter listener to all sessions
# do_orm_execute is a Session event, but AsyncSession uses Session internally
event.listen(Session, "do_orm_execute", _add_tenant_filter)


class Base(DeclarativeBase):
    pass
