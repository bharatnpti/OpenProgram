"""Record how a sync run ended, beside the cursor it already keeps.

A successful run has always written ``last_checked_at`` into its cursor
metadata. A failed run wrote nothing, so a broken sync looked exactly like one
that never ran. These helpers add the failure side: the time and a closed error
category, written into the same cursor row without moving the cursor value, so
the next run still resumes from the last good position and reruns stay
idempotent.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

import structlog

from core.domain.errors import ProviderConfigurationError, ProviderUnavailable, SecretNotFound
from core.domain.integrations import SyncCursor
from core.domain.sync_status import (
    LAST_CHECKED_AT_KEY,
    LAST_ERROR_KIND_KEY,
    LAST_FAILED_AT_KEY,
    LAST_ITEM_COUNT_KEY,
    PROVIDER_START_FAILURE_PREFIX,
    PROVIDER_START_FAILURE_REASONS,
    SyncErrorKind,
)
from core.ports.repositories import SyncCursorRepository

_logger = structlog.get_logger(__name__)


def classify_sync_error(error: BaseException) -> SyncErrorKind:
    """Map an exception to a category without keeping any of its text."""
    if isinstance(error, ProviderConfigurationError | SecretNotFound):
        return SyncErrorKind.CREDENTIALS
    if isinstance(error, ProviderUnavailable):
        # Adapters raise a plain ProviderUnavailable("... credentials are not
        # configured") when no token is set. The phrase only picks the category;
        # the message itself is never stored.
        if "credentials" in str(error).lower():
            return SyncErrorKind.CREDENTIALS
        return SyncErrorKind.PROVIDER_UNAVAILABLE
    return SyncErrorKind.UNEXPECTED


def provider_start_failure_message(error: BaseException) -> str:
    """A reader-safe reason a provider could not be built.

    Only fixed phrases and the exception's class name are used. The message is
    dropped: building a provider fails on exactly the values -- a token, a base
    URL, a key -- that must never be echoed back.
    """
    reason = PROVIDER_START_FAILURE_REASONS[classify_sync_error(error)]
    error_type = type(error).__name__
    if not error_type.isidentifier():
        error_type = "Error"
    return f"{PROVIDER_START_FAILURE_PREFIX}: {reason} ({error_type})"


def succeeded_cursor(cursor: SyncCursor, checked_at: datetime, item_count: int) -> SyncCursor:
    return SyncCursor(
        value=cursor.value,
        updated_at=cursor.updated_at,
        metadata={
            **cursor.metadata,
            LAST_CHECKED_AT_KEY: checked_at.isoformat(),
            LAST_ITEM_COUNT_KEY: item_count,
        },
    )


def failed_cursor(cursor: SyncCursor, failed_at: datetime, error: BaseException) -> SyncCursor:
    """The same cursor position, with the failure noted in its metadata."""
    return SyncCursor(
        value=cursor.value,
        updated_at=cursor.updated_at,
        metadata={
            **cursor.metadata,
            LAST_FAILED_AT_KEY: failed_at.isoformat(),
            LAST_ERROR_KIND_KEY: classify_sync_error(error).value,
        },
    )


async def record_sync_failure(
    repository: SyncCursorRepository,
    *,
    tenant_id: str,
    connector: str,
    scope: str,
    cursor: SyncCursor,
    failed_at: datetime,
    error: BaseException,
) -> None:
    """Best-effort: a failure to record must never hide the original error."""
    try:
        await repository.record_cursor(
            tenant_id, connector, scope, failed_cursor(cursor, failed_at, error)
        )
    except Exception:  # pragma: no cover - defensive; the caller re-raises the real error
        _logger.warning(
            "sync_failure_not_recorded",
            tenant_id=tenant_id,
            connector=connector,
            scope=scope,
        )


@asynccontextmanager
async def recording_sync_failure(
    repository: SyncCursorRepository,
    *,
    tenant_id: str,
    connector: str,
    scope: str,
    cursor: SyncCursor,
    attempted_at: datetime,
) -> AsyncIterator[None]:
    """Note a failed run in its cursor row, then let the error propagate.

    The workflow engine still sees the original exception, so its retries and
    failure handling are unchanged.
    """
    try:
        yield
    except Exception as error:
        await record_sync_failure(
            repository,
            tenant_id=tenant_id,
            connector=connector,
            scope=scope,
            cursor=cursor,
            failed_at=attempted_at,
            error=error,
        )
        raise
