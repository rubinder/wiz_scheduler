"""Automatic tenant isolation via ORM filtering and context variables."""
from contextvars import ContextVar
from contextlib import contextmanager
from typing import Optional

tenant_context: ContextVar[Optional[list[str]]] = ContextVar("tenant_context", default=None)


def set_tenant_context(company_ids: list[str]) -> None:
    """Set the current request's allowed company IDs in the context."""
    tenant_context.set(company_ids)


def get_tenant_context() -> Optional[list[str]]:
    """Get the current request's allowed company IDs."""
    return tenant_context.get()


@contextmanager
def tenant_scope_disabled():
    """Escape hatch for legitimate cross-tenant operations.

    Use this for background jobs, data retention sweeps, reports, and webhooks
    that legitimately operate across tenants.

    Example:
        with tenant_scope_disabled():
            # This query will not be filtered by company_id
            await db.execute(select(User))
    """
    token = tenant_context.set(None)
    try:
        yield
    finally:
        tenant_context.reset(token)
