"""Per-source sync status for the admin "Data sources" view.

Read-only. It joins three things that are already recorded:

* the configured targets -- project/pod graph metadata first, then the legacy
  environment lists, exactly as the runtime sync resolves them;
* each target's sync cursor row, whose metadata a run updates on success
  (``last_checked_at``) and on failure (``last_failed_at`` + error category);
* the newest fact each source appended, as a freshness signal independent of
  the cursor.

It never calls a provider, so opening the view can't itself fail on, or
spend quota against, Jira or GitHub. The one thing it is told about providers
is whether each could be *built* (``SyncStatusConfig.provider_start_errors``):
a provider that fails to build fails every run before it records anything, so
without that a broken source would read as "never synced".
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta

from core.application.directory_sync_service import (
    DIRECTORY_SYNC_CONNECTOR,
    DIRECTORY_SYNC_SCOPE,
)
from core.application.sync_targets import (
    RuntimeSyncTargetResolver,
    RuntimeSyncTargets,
    SyncTargetValidationError,
)
from core.domain.graph import JsonScalar, NodeKind
from core.domain.integrations import SyncCursor, SyncCursorRecord
from core.domain.sync_status import (
    LAST_CHECKED_AT_KEY,
    LAST_ERROR_KIND_KEY,
    LAST_FAILED_AT_KEY,
    LAST_ITEM_COUNT_KEY,
    SyncErrorKind,
    SyncHealth,
    SyncOutcome,
    SyncSource,
    SyncSourceStatus,
    SyncStatusConfig,
    SyncStatusReport,
    SyncTargetOrigin,
    SyncTargetStatus,
)
from core.domain.workflows import SyncDispatchInput
from core.ports.repositories import GraphRepository, SyncCursorRepository, TimeSeriesRepository

ISSUE_CONNECTOR = "issue"
VCS_CONNECTOR = "vcs"
ISSUE_FACT_SOURCES = ("issue",)
VCS_FACT_SOURCES = ("vcs_commit", "vcs_pull_request")

# A target is stale once it has missed roughly three scheduled runs, with a
# floor so a 15-minute schedule doesn't flag a single slow run.
STALE_INTERVAL_MULTIPLIER = 3
MIN_STALE_AFTER = timedelta(hours=1)
DEFAULT_STALE_AFTER = timedelta(days=1)

_SOURCE_HEALTH_ORDER = (
    SyncHealth.FAILING,
    SyncHealth.NEVER_SYNCED,
    SyncHealth.STALE,
)


class SyncStatusService:
    def __init__(
        self,
        *,
        graph_repository: GraphRepository,
        cursor_repository: SyncCursorRepository,
        time_series_repository: TimeSeriesRepository,
        config: SyncStatusConfig,
    ) -> None:
        self._graph_repository = graph_repository
        self._cursor_repository = cursor_repository
        self._time_series_repository = time_series_repository
        self._config = config

    async def status(self, tenant_id: str, *, now: datetime | None = None) -> SyncStatusReport:
        generated_at = _aware(now or datetime.now(tz=UTC))
        cursors = _cursors_by_connector(await self._cursor_repository.list_cursors(tenant_id))
        runtime_targets, config_error = await self._runtime_targets(tenant_id)
        names = await self._node_names(tenant_id)
        config = self._config

        issue_targets, issue_origin = _effective_targets(
            runtime_targets.issue_dispatches if runtime_targets else (),
            config.legacy_issue_targets,
            config_error,
        )
        vcs_targets, vcs_origin = _effective_targets(
            runtime_targets.vcs_dispatches if runtime_targets else (),
            config.legacy_vcs_targets,
            config_error,
        )
        return SyncStatusReport(
            generated_at=generated_at,
            sources=(
                _source_status(
                    SyncSource.ISSUE_TRACKER,
                    provider=config.issue_tracker_provider,
                    simulated=config.issue_tracker_provider in config.simulated_providers,
                    schedule=config.issue_sync_cron,
                    origin=issue_origin,
                    targets=_dispatch_targets(
                        issue_targets,
                        cursors.get(ISSUE_CONNECTOR, {}),
                        names,
                        all_configured=config_error is not None,
                        stale_after=stale_after_for_cron(config.issue_sync_cron),
                        now=generated_at,
                    ),
                    newest_item_at=await self._newest_fact_at(tenant_id, ISSUE_FACT_SOURCES),
                    config_error=config_error,
                    provider_error=config.provider_start_errors.get(SyncSource.ISSUE_TRACKER),
                ),
                _source_status(
                    SyncSource.VCS,
                    provider=config.vcs_provider,
                    simulated=config.vcs_provider in config.simulated_providers,
                    schedule=config.vcs_sync_cron,
                    origin=vcs_origin,
                    targets=_dispatch_targets(
                        vcs_targets,
                        cursors.get(VCS_CONNECTOR, {}),
                        names,
                        all_configured=config_error is not None,
                        stale_after=stale_after_for_cron(config.vcs_sync_cron),
                        now=generated_at,
                    ),
                    newest_item_at=await self._newest_fact_at(tenant_id, VCS_FACT_SOURCES),
                    config_error=config_error,
                    provider_error=config.provider_start_errors.get(SyncSource.VCS),
                ),
                _calendar_status(
                    config.calendar_provider,
                    simulated=config.calendar_provider in config.simulated_providers,
                ),
                _source_status(
                    SyncSource.DIRECTORY,
                    provider=config.directory_provider,
                    simulated=config.directory_provider in config.simulated_providers,
                    schedule=config.directory_sync_cron,
                    origin=SyncTargetOrigin.WORKSPACE,
                    targets=(
                        target_status(
                            scope=DIRECTORY_SYNC_SCOPE,
                            label="Chat workspace members",
                            cursor=cursors.get(DIRECTORY_SYNC_CONNECTOR, {}).get(
                                DIRECTORY_SYNC_SCOPE
                            ),
                            stale_after=stale_after_for_cron(config.directory_sync_cron),
                            now=generated_at,
                        ),
                    ),
                    newest_item_at=None,
                    config_error=None,
                    provider_error=config.provider_start_errors.get(SyncSource.DIRECTORY),
                ),
            ),
        )

    async def _runtime_targets(
        self, tenant_id: str
    ) -> tuple[RuntimeSyncTargets | None, str | None]:
        try:
            targets = await RuntimeSyncTargetResolver(self._graph_repository).resolve(tenant_id)
        except SyncTargetValidationError as exc:
            # The message names the admin's own pod and repo ids, never provider
            # content. The runtime sync fails the same way, for both connectors.
            return None, str(exc)
        return targets, None

    async def _node_names(self, tenant_id: str) -> dict[str, str]:
        names: dict[str, str] = {}
        for kind in (NodeKind.PROJECT, NodeKind.POD):
            for node in await self._graph_repository.list_nodes(tenant_id, kind):
                names[node.id] = node.name
        return names

    async def _newest_fact_at(self, tenant_id: str, sources: Sequence[str]) -> datetime | None:
        facts = await self._time_series_repository.list_recent_facts(
            tenant_id, sources=sources, limit=1
        )
        return _aware(facts[0].observed_at) if facts else None


def target_status(
    *,
    scope: str,
    label: str,
    cursor: SyncCursor | None,
    stale_after: timedelta,
    now: datetime,
    detail: str | None = None,
    configured: bool = True,
) -> SyncTargetStatus:
    """One target's status from its cursor row (``None`` = never ran)."""
    metadata: Mapping[str, JsonScalar] = cursor.metadata if cursor is not None else {}
    last_synced_at = _metadata_datetime(metadata, LAST_CHECKED_AT_KEY)
    last_failed_at = _metadata_datetime(metadata, LAST_FAILED_AT_KEY)
    failed_last = last_failed_at is not None and (
        last_synced_at is None or last_failed_at > last_synced_at
    )
    if failed_last:
        health = SyncHealth.FAILING
    elif last_synced_at is None:
        health = SyncHealth.NEVER_SYNCED
    elif now - last_synced_at > stale_after:
        health = SyncHealth.STALE
    else:
        health = SyncHealth.HEALTHY
    outcome = (
        SyncOutcome.FAILED
        if failed_last
        else SyncOutcome.SUCCEEDED
        if last_synced_at is not None
        else None
    )
    item_count = metadata.get(LAST_ITEM_COUNT_KEY)
    return SyncTargetStatus(
        scope=scope,
        label=label,
        detail=detail,
        configured=configured,
        health=health,
        last_synced_at=last_synced_at,
        last_attempt_at=_latest((last_synced_at, last_failed_at)),
        last_outcome=outcome,
        last_error=_metadata_error_kind(metadata) if failed_last else None,
        items_synced=item_count if isinstance(item_count, int) else None,
    )


def stale_after_for_cron(cron: str) -> timedelta:
    interval = cron_interval(cron)
    if interval is None:
        return DEFAULT_STALE_AFTER
    return max(interval * STALE_INTERVAL_MULTIPLIER, MIN_STALE_AFTER)


def cron_interval(cron: str) -> timedelta | None:
    """Average gap between runs of a cron expression, or ``None`` if unsure.

    Covers the minute/hour shapes sync schedules use (``*/15 * * * *``,
    ``0 * * * *``, ``0 */6 * * *``, ``0 8 * * *``). A six-field expression's
    leading seconds field is ignored. Day-of-month and month restrictions are
    not estimated; day-of-week restrictions are treated as daily.
    """
    fields = cron.split()
    if len(fields) == 6:
        fields = fields[1:]
    if len(fields) != 5:
        return None
    minute, hour, day_of_month, month, _day_of_week = fields
    if day_of_month != "*" or month != "*":
        return None
    minute_runs = _cron_field_runs(minute, 60)
    hour_runs = _cron_field_runs(hour, 24)
    if minute_runs is None or hour_runs is None:
        return None
    return timedelta(minutes=24 * 60 / (minute_runs * hour_runs))


def _cron_field_runs(field: str, size: int) -> int | None:
    total = 0
    for part in field.split(","):
        runs = _cron_part_runs(part, size)
        if runs is None:
            return None
        total += runs
    return min(total, size) if total > 0 else None


def _cron_part_runs(part: str, size: int) -> int | None:
    base, _, step_text = part.partition("/")
    step = 1
    if step_text:
        if not step_text.isdigit() or int(step_text) == 0:
            return None
        step = int(step_text)
    if base == "*":
        span = size
    elif base.isdigit():
        span = 1 if not step_text else size - int(base)
    else:
        start, _, end = base.partition("-")
        if not (start.isdigit() and end.isdigit()) or int(end) < int(start):
            return None
        span = int(end) - int(start) + 1
    return max(math.ceil(span / step), 1) if span > 0 else None


def _source_status(
    source: SyncSource,
    *,
    provider: str,
    simulated: bool,
    schedule: str,
    origin: SyncTargetOrigin,
    targets: tuple[SyncTargetStatus, ...],
    newest_item_at: datetime | None,
    config_error: str | None,
    provider_error: str | None,
) -> SyncSourceStatus:
    configured = [target for target in targets if target.configured]
    failing = [target for target in configured if target.health is SyncHealth.FAILING]
    latest_failure = max(failing, key=lambda target: target.last_attempt_at or _EPOCH, default=None)
    return SyncSourceStatus(
        source=source,
        provider=provider,
        simulated=simulated,
        sync_enabled=True,
        schedule=schedule,
        stale_after_minutes=int(stale_after_for_cron(schedule).total_seconds() // 60),
        target_origin=origin,
        health=_source_health(configured, config_error, provider_error),
        last_synced_at=_latest(target.last_synced_at for target in targets),
        last_attempt_at=_latest(target.last_attempt_at for target in targets),
        last_error=(
            latest_failure.last_error
            if latest_failure and not (config_error or provider_error)
            else None
        ),
        newest_item_at=newest_item_at,
        config_error=config_error,
        provider_error=provider_error,
        targets=targets,
    )


def _source_health(
    configured: Sequence[SyncTargetStatus],
    config_error: str | None,
    provider_error: str | None,
) -> SyncHealth:
    # A provider that can't be built fails every run before it records anything,
    # so no cursor will ever say so: report it ahead of what the cursors show.
    if config_error is not None or provider_error is not None:
        return SyncHealth.FAILING
    if not configured:
        return SyncHealth.NOT_CONFIGURED
    healths = {target.health for target in configured}
    for health in _SOURCE_HEALTH_ORDER:
        if health in healths:
            return health
    return SyncHealth.HEALTHY


def _calendar_status(provider: str, *, simulated: bool) -> SyncSourceStatus:
    # Calendar read-sync is deliberately a no-op: availability is read live
    # from the provider before a reminder goes out, so there is nothing to sync.
    return SyncSourceStatus(
        source=SyncSource.CALENDAR,
        provider=provider,
        simulated=simulated,
        sync_enabled=False,
        schedule=None,
        stale_after_minutes=None,
        target_origin=SyncTargetOrigin.NONE,
        health=SyncHealth.DISABLED,
    )


def _effective_targets(
    runtime: tuple[SyncDispatchInput, ...],
    legacy: tuple[SyncDispatchInput, ...],
    config_error: str | None,
) -> tuple[tuple[SyncDispatchInput, ...], SyncTargetOrigin]:
    # Same precedence as the runtime sync: graph config wins, then env lists.
    if config_error is not None:
        return (), SyncTargetOrigin.RUNTIME_CONFIG
    if runtime:
        return runtime, SyncTargetOrigin.RUNTIME_CONFIG
    if legacy:
        return legacy, SyncTargetOrigin.ENVIRONMENT
    return (), SyncTargetOrigin.NONE


def _dispatch_targets(
    dispatches: tuple[SyncDispatchInput, ...],
    cursors: Mapping[str, SyncCursor],
    names: Mapping[str, str],
    *,
    all_configured: bool,
    stale_after: timedelta,
    now: datetime,
) -> tuple[SyncTargetStatus, ...]:
    """Configured targets first, then cursor rows no current target owns.

    A leftover row (an ad-hoc admin sync, or a JQL that has since changed) is
    still shown so its history isn't hidden, but it is marked as not
    configured and doesn't count towards the source's health. When the target
    config can't be read at all, every row is shown as configured.
    """
    statuses: list[SyncTargetStatus] = []
    seen: set[str] = set()
    for dispatch in dispatches:
        if dispatch.scope in seen:
            continue
        seen.add(dispatch.scope)
        label, detail = _dispatch_label(dispatch, names)
        statuses.append(
            target_status(
                scope=dispatch.scope,
                label=label,
                detail=detail,
                cursor=cursors.get(dispatch.scope),
                stale_after=stale_after,
                now=now,
            )
        )
    for scope, cursor in sorted(cursors.items()):
        if scope in seen:
            continue
        statuses.append(
            target_status(
                scope=scope,
                label=_scope_label(scope, names),
                cursor=cursor,
                stale_after=stale_after,
                now=now,
                configured=all_configured,
            )
        )
    return tuple(statuses)


def _dispatch_label(
    dispatch: SyncDispatchInput, names: Mapping[str, str]
) -> tuple[str, str | None]:
    payload = dispatch.payload
    jql = payload.get("jql")
    target_id = payload.get("target_node_id")
    if isinstance(jql, str) and isinstance(target_id, str):
        kind = payload.get("target_node_kind")
        return _node_label(str(kind or "project"), target_id, names), jql
    containers = payload.get("container_ids")
    if isinstance(containers, str) and containers:
        linked = ", ".join(names.get(item, item) for item in containers.split(",") if item)
        return _scope_label(dispatch.scope, names), f"Linked to {linked}"
    return _scope_label(dispatch.scope, names), None


def _scope_label(scope: str, names: Mapping[str, str]) -> str:
    prefix, _, rest = scope.partition(":")
    if prefix == "project" and rest:
        return _node_label("project", rest, names)
    if prefix == "repo" and rest:
        return rest
    if prefix == "query":
        kind, _, remainder = rest.partition(":")
        node_id = remainder.rsplit(":", 1)[0] if ":" in remainder else remainder
        if kind and node_id:
            return _node_label(kind, node_id, names)
    return scope


def _node_label(kind: str, node_id: str, names: Mapping[str, str]) -> str:
    return f"{kind.capitalize()} {names.get(node_id, node_id)}"


def _cursors_by_connector(
    records: Iterable[SyncCursorRecord],
) -> dict[str, dict[str, SyncCursor]]:
    grouped: dict[str, dict[str, SyncCursor]] = {}
    for record in records:
        grouped.setdefault(record.connector, {})[record.scope] = record.cursor
    return grouped


def _metadata_datetime(metadata: Mapping[str, JsonScalar], key: str) -> datetime | None:
    value = metadata.get(key)
    if not isinstance(value, str):
        return None
    try:
        return _aware(datetime.fromisoformat(value))
    except ValueError:
        return None


def _metadata_error_kind(metadata: Mapping[str, JsonScalar]) -> SyncErrorKind:
    value = metadata.get(LAST_ERROR_KIND_KEY)
    try:
        return SyncErrorKind(value) if isinstance(value, str) else SyncErrorKind.UNEXPECTED
    except ValueError:
        return SyncErrorKind.UNEXPECTED


def _latest(values: Iterable[datetime | None]) -> datetime | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
