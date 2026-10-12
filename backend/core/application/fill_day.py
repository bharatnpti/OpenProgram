"""Fill the day from Jira for the people the check-in never reached.

The fan-out asks every member; a check-in that reached someone closes with
their reply, or with a day inferred, stale or unknown when it went unanswered.
Someone it never reached -- the send is switched off, or the send to them did
not go through -- had no day at all. This pass gives them one, once a day, on
the reconcile tick: inferred from what moved in the tracker and Git since their
last answer (``core.domain.observed_day``), else stale or unknown. It is never
green, and it says in its first words that they were not asked or not reached.

Who is filled, for a day:

- every member the fan-out would ask (``developers_without_checkin``), with a
  member record not deleted by then;
- whose check-in days include it;
- past their fill time: their check-in time plus their nudge wait, read in
  their own time zone, when a check-in that reached them would have had its
  first nudge (by then the reconcile has retried a failed send for hours);
- with no status for the day and no check-in run for it (one that was sent is
  closed by its own ladder);
- and not on leave by their calendar.

The tenant switch "Fill the day from Jira for people who were not asked"
(Admin › Check-ins) is on by default; off, those people stay without a day.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

import structlog

from core.application.availability import AvailabilityService
from core.application.observed_day_service import ObservedDayReader, checkin_time
from core.application.status_summaries import NOT_ASKED_LEAD, NOT_REACHED_LEAD
from core.domain.graph import DELETED_ON_METADATA_KEY, JsonScalar, NodeKind
from core.domain.integrations import UserRef
from core.domain.observed_day import DEFAULT_FILL_DAY, FillDaySetting
from core.domain.status import DeveloperStatus, resolve_timezone
from core.ports.repositories import FillDayRepository, GraphRepository, StatusRepository

_logger = structlog.get_logger(__name__)


class DayFiller(Protocol):
    """Records a filled day (``StatusCollector.fill_day``)."""

    async def fill_day(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        as_of: date,
        lead: str,
        until: datetime | None = None,
    ) -> DeveloperStatus: ...


#: Told of each save (tenant, on): the registry adds or removes the fill's schedule.
FillDaySwitched = Callable[[str, bool], Awaitable[None]]


class FillDaySettingsService:
    """The tenant's switch: what is in force, and saving it with who and when."""

    def __init__(
        self,
        repository: FillDayRepository,
        *,
        clock: Callable[[], datetime] | None = None,
        on_change: FillDaySwitched | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        self._on_change = on_change

    async def effective(self, tenant_id: str) -> FillDaySetting:
        setting, _ = await self.read(tenant_id)
        return setting

    async def read(self, tenant_id: str) -> tuple[FillDaySetting, bool]:
        """The switch in force, and whether an admin saved it (else the default, on)."""
        stored = await self._repository.get_fill_day(tenant_id)
        if stored is not None:
            return stored, True
        return FillDaySetting(tenant_id=tenant_id, enabled=DEFAULT_FILL_DAY), False

    async def set(self, tenant_id: str, enabled: bool, *, actor: str) -> FillDaySetting:
        setting = FillDaySetting(
            tenant_id=tenant_id, enabled=enabled, updated_at=self._clock(), updated_by=actor
        )
        await self._repository.save_fill_day(setting)
        if self._on_change is not None:
            try:
                await self._on_change(tenant_id, enabled)
            except Exception as exc:  # noqa: BLE001 - the switch is saved; each run reads it
                _logger.warning(
                    "checkin_fill_schedule_not_updated",
                    tenant_id=tenant_id,
                    enabled=enabled,
                    error_type=type(exc).__name__,
                )
        return setting


@dataclass(frozen=True, kw_only=True)
class FilledDay:
    developer_id: str
    as_of: date
    status: DeveloperStatus


class FillDayPass:
    def __init__(
        self,
        *,
        status_repository: StatusRepository,
        graph_repository: GraphRepository,
        filler: DayFiller,
        reader: ObservedDayReader,
        settings: FillDaySettingsService,
        availability: AvailabilityService | None = None,
        sends_on: bool = True,
        default_timezone: str = "UTC",
    ) -> None:
        self._statuses = status_repository
        self._graph = graph_repository
        self._filler = filler
        self._reader = reader
        self._settings = settings
        self._availability = availability
        self._sends_on = sends_on
        self._default_timezone = default_timezone

    async def run(self, tenant_id: str, now: datetime) -> tuple[FilledDay, ...]:
        """Fill every day due by ``now``: today's, and yesterday's not yet filled."""
        if not (await self._settings.effective(tenant_id)).enabled:
            return ()
        today = now.astimezone(resolve_timezone(None, self._default_timezone)).date()
        filled: list[FilledDay] = []
        for day in (today - timedelta(days=1), today):
            for developer_id in await self._statuses.developers_without_checkin(tenant_id, day):
                status = await self._fill_one(tenant_id, developer_id, day, now)
                if status is not None:
                    filled.append(FilledDay(developer_id=developer_id, as_of=day, status=status))
        return tuple(filled)

    async def _fill_one(
        self, tenant_id: str, developer_id: str, day: date, now: datetime
    ) -> DeveloperStatus | None:
        node = await self._graph.get_node(tenant_id, developer_id)
        if node is None or node.kind is not NodeKind.DEVELOPER or _deleted_by(node.metadata, day):
            return None
        preference = await self._reader.preference(tenant_id, developer_id)
        if day.weekday() not in set(preference.weekdays):
            return None
        due = checkin_time(preference, day) + timedelta(seconds=preference.reply_wait_seconds)
        if now < due:
            return None
        latest = await self._statuses.latest_developer_status(tenant_id, developer_id, day)
        if latest is not None and latest.as_of == day:
            return None
        if await self._statuses.checkin_schedule_run(tenant_id, developer_id, day) is not None:
            return None
        if not await self._available(tenant_id, developer_id, day):
            return None
        return await self._filler.fill_day(
            tenant_id=tenant_id,
            developer_id=developer_id,
            as_of=day,
            lead=NOT_REACHED_LEAD if self._sends_on else NOT_ASKED_LEAD,
            until=now,
        )

    async def _available(self, tenant_id: str, developer_id: str, day: date) -> bool:
        """Not on leave, by the calendar the nudge reads; a calendar that fails says nothing."""
        if self._availability is None:
            return True
        try:
            result = await self._availability.availability_for(
                UserRef(tenant_id=tenant_id, external_id=developer_id),
                day,
                default_timezone=self._default_timezone,
            )
        except Exception as exc:  # noqa: BLE001 - one calendar read never stops the pass
            _logger.warning(
                "fill_day_calendar_unreadable",
                tenant_id=tenant_id,
                developer_id=developer_id,
                error_type=type(exc).__name__,
            )
            return True
        return result.available


def _deleted_by(metadata: Mapping[str, JsonScalar], day: date) -> bool:
    value = metadata.get(DELETED_ON_METADATA_KEY)
    if not isinstance(value, str) or not value:
        return False
    try:
        return date.fromisoformat(value[:10]) <= day
    except ValueError:
        return False
