from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from uuid import uuid4

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def current_correlation_id() -> str:
    existing = correlation_id_var.get()
    if existing is not None:
        return existing
    generated = str(uuid4())
    correlation_id_var.set(generated)
    return generated


@asynccontextmanager
async def correlation_scope(correlation_id: str) -> AsyncIterator[None]:
    token = correlation_id_var.set(correlation_id)
    try:
        yield
    finally:
        correlation_id_var.reset(token)
