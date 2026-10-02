"""Read model for "is each data source actually syncing?".

Everything here is derived from what sync runs already record (the per-target
sync cursor and the facts they append) plus the configured targets. Nothing is
inferred that was not recorded: a source with no recorded run says so instead
of looking healthy.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from core.domain.workflows import SyncDispatchInput


class SyncSource(StrEnum):
    ISSUE_TRACKER = "issue_tracker"
    VCS = "vcs"
    CALENDAR = "calendar"
    DIRECTORY = "directory"


class SyncHealth(StrEnum):
    """How much a reader can trust the data a source feeds in.

    ``DISABLED`` is a source that by design is not synced (the calendar is read
    live). ``NOT_CONFIGURED`` has no recurring target, so nothing will refresh
    it. Neither is ever reported as healthy.
    """

    HEALTHY = "healthy"
    STALE = "stale"
    FAILING = "failing"
    NEVER_SYNCED = "never_synced"
    NOT_CONFIGURED = "not_configured"
    DISABLED = "disabled"


class SyncOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class SyncErrorKind(StrEnum):
    """A closed set of failure categories.

    A sync failure is stored and shown only as one of these -- never the
    exception text -- so a provider error body (which can echo a token, a URL
    or issue content) can never reach the status view.
    """

    CREDENTIALS = "credentials"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    UNEXPECTED = "unexpected"


class SyncTargetOrigin(StrEnum):
    """Where a source's recurring targets come from."""

    RUNTIME_CONFIG = "runtime_config"
    ENVIRONMENT = "environment"
    WORKSPACE = "workspace"
    NONE = "none"


SYNC_ERROR_MESSAGES: dict[SyncErrorKind, str] = {
    SyncErrorKind.CREDENTIALS: "Credentials are missing or were rejected by the provider.",
    SyncErrorKind.PROVIDER_UNAVAILABLE: (
        "The provider could not be reached or returned a response the sync could not read."
    ),
    SyncErrorKind.UNEXPECTED: "The sync stopped on an unexpected error. Check the worker logs.",
}

# A provider that cannot be built is reported as "<prefix>: <reason> (<ErrorType>)",
# assembled only from these fixed phrases and the exception's class name -- never
# its message, which can carry a credential, a URL or a config value.
PROVIDER_START_FAILURE_PREFIX = "Provider could not start"
PROVIDER_START_FAILURE_REASONS: dict[SyncErrorKind, str] = {
    SyncErrorKind.CREDENTIALS: "missing or invalid credential",
    SyncErrorKind.PROVIDER_UNAVAILABLE: "provider unavailable",
    SyncErrorKind.UNEXPECTED: "unexpected error",
}

# Cursor metadata keys a sync run writes. ``last_checked_at``/``last_item_count``
# predate this module; the failure keys sit beside them in the same JSON so no
# schema change is needed and the cursor value itself never moves on failure.
LAST_CHECKED_AT_KEY = "last_checked_at"
LAST_ITEM_COUNT_KEY = "last_item_count"
LAST_FAILED_AT_KEY = "last_failed_at"
LAST_ERROR_KIND_KEY = "last_error_kind"


@dataclass(frozen=True, kw_only=True)
class SyncStatusConfig:
    """Plain config values the status read needs, decoupled from Settings.

    ``*_provider`` names are the effective provider the registry builds, so a
    built-in sample provider is reported as such. Legacy targets are the
    environment fallback the runtime sync uses when no project/pod config sets
    one. ``provider_start_errors`` holds the sanitised reason for each source
    whose provider could not even be built; no sync run ever records that,
    because a run that cannot build its provider never reaches the cursor.
    """

    issue_tracker_provider: str
    vcs_provider: str
    calendar_provider: str
    directory_provider: str
    issue_sync_cron: str
    vcs_sync_cron: str
    directory_sync_cron: str
    legacy_issue_targets: tuple[SyncDispatchInput, ...] = ()
    legacy_vcs_targets: tuple[SyncDispatchInput, ...] = ()
    # Built-in providers that serve canned data instead of a live system.
    simulated_providers: frozenset[str] = frozenset({"fake"})
    provider_start_errors: Mapping[SyncSource, str] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class SyncTargetStatus:
    scope: str
    label: str
    detail: str | None = None
    configured: bool = True
    health: SyncHealth
    last_synced_at: datetime | None = None
    last_attempt_at: datetime | None = None
    last_outcome: SyncOutcome | None = None
    last_error: SyncErrorKind | None = None
    items_synced: int | None = None


@dataclass(frozen=True, kw_only=True)
class SyncSourceStatus:
    source: SyncSource
    provider: str
    simulated: bool
    sync_enabled: bool
    schedule: str | None
    stale_after_minutes: int | None
    target_origin: SyncTargetOrigin
    health: SyncHealth
    last_synced_at: datetime | None = None
    last_attempt_at: datetime | None = None
    last_error: SyncErrorKind | None = None
    newest_item_at: datetime | None = None
    config_error: str | None = None
    # Why the provider could not be built: fixed text and the error type only.
    provider_error: str | None = None
    targets: tuple[SyncTargetStatus, ...] = field(default_factory=tuple)


@dataclass(frozen=True, kw_only=True)
class SyncStatusReport:
    generated_at: datetime
    sources: tuple[SyncSourceStatus, ...]
