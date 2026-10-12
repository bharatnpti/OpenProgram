"""Read a person's observed day: the facts the digest needs, gathered for one member.

``core.domain.observed_day`` is the pure digest. This reads its inputs: the
tasks the member holds (assignment edges), each task's own facts (issue and
``issue_change`` facts, task updates), the tenant's changes in the window (for
the issues the member changed but does not hold, and assignments), the
member's Git facts and their last answer. The window opens at the later of the
member's last confirmed or partial answer and their check-in time on their
previous check-in day.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta

from core.application.merge_request_links import MERGE_REQUEST_FACT_SOURCE
from core.application.sync_services import ISSUE_CHANGE_FACT_SOURCE, ISSUE_FACT_SOURCE
from core.application.task_update_service import TASK_UPDATE_FACT_SOURCE
from core.domain.delivery import DEFAULT_STAGE_MAPPING, StageMapping
from core.domain.graph import EdgeKind, EntityRef, FactEvent, NodeKind
from core.domain.observed_day import (
    COMMIT_SOURCE,
    QUIET_AFTER_DAYS,
    ObservedDay,
    digest,
    window_start,
)
from core.domain.status import (
    CheckInDefaults,
    EffectiveCheckInPreference,
    effective_checkin_preference,
    resolve_timezone,
)
from core.ports.delivery import DeliverySettingsRepository
from core.ports.repositories import GraphRepository, StatusRepository, TimeSeriesRepository

#: How far back a task's history is read to tell how long it has not moved.
HISTORY_LOOKBACK = timedelta(days=30)
#: The most tracker changes of the tenant read for one window.
_TENANT_CHANGES_LIMIT = 5000
#: The fact source a check-in reply leaves on the person.
_CHECKIN_FACT_SOURCE = "checkin"
_ANSWER_SOURCES = frozenset({"confirmed", "partial"})
_GIT_SOURCES = frozenset({MERGE_REQUEST_FACT_SOURCE, COMMIT_SOURCE})


class ObservedDayReader:
    def __init__(
        self,
        *,
        graph_repository: GraphRepository,
        time_series_repository: TimeSeriesRepository,
        status_repository: StatusRepository,
        delivery_settings_repository: DeliverySettingsRepository | None = None,
        checkin_defaults: CheckInDefaults | None = None,
        quiet_after_days: int = QUIET_AFTER_DAYS,
    ) -> None:
        self._graph = graph_repository
        self._facts = time_series_repository
        self._statuses = status_repository
        self._delivery_settings = delivery_settings_repository
        self._defaults = checkin_defaults or CheckInDefaults()
        self._quiet_after_days = quiet_after_days

    async def preference(self, tenant_id: str, member_id: str) -> EffectiveCheckInPreference:
        return effective_checkin_preference(
            tenant_id,
            member_id,
            await self._statuses.checkin_preference_for(tenant_id, member_id),
            self._defaults,
        )

    async def mapping(self, tenant_id: str) -> StageMapping:
        if self._delivery_settings is None:
            return DEFAULT_STAGE_MAPPING
        stored = await self._delivery_settings.get(tenant_id)
        return stored.mapping if stored is not None else DEFAULT_STAGE_MAPPING

    async def digest_for(
        self,
        tenant_id: str,
        member_id: str,
        *,
        as_of: date,
        until: datetime,
        extra_keys: Iterable[str] = (),
    ) -> ObservedDay:
        """What moved on the member's work for ``as_of``, read up to ``until``.

        ``extra_keys`` are more tasks the member holds, such as the tracker's
        live list of their open issues at a check-in's close.
        """
        preference = await self.preference(tenant_id, member_id)
        own = await self._facts.list_facts(
            tenant_id,
            EntityRef(tenant_id=tenant_id, kind=NodeKind.DEVELOPER, id=member_id),
            until - HISTORY_LOOKBACK,
        )
        since = window_start(
            previous_checkin_at(preference, as_of),
            _last_answer_at(own, until),
            until=until,
        )
        keys = await self.assigned_keys(tenant_id, member_id, as_of)
        keys.update(key.upper() for key in extra_keys if key)
        tenant_changes = await self._facts.list_recent_facts(
            tenant_id,
            since=since,
            sources=(ISSUE_CHANGE_FACT_SOURCE,),
            limit=_TENANT_CHANGES_LIMIT,
        )
        # Tasks assigned to or away from the member in the window are theirs for it.
        for fact in tenant_changes:
            key = fact.payload.get("key")
            if (
                isinstance(key, str)
                and fact.payload.get("field") == "assignee"
                and member_id
                in (fact.payload.get("from_member_id"), fact.payload.get("to_member_id"))
            ):
                keys.add(key.upper())
        task_facts: list[FactEvent] = []
        for key in sorted(keys):
            task_facts.extend(
                await self._facts.list_facts(
                    tenant_id,
                    EntityRef(tenant_id=tenant_id, kind=NodeKind.TASK, id=key),
                    until - HISTORY_LOOKBACK,
                )
            )
        held = {_fact_key(fact) for fact in task_facts}
        changes = [
            *(fact for fact in task_facts if fact.source == ISSUE_CHANGE_FACT_SOURCE),
            # The member's changes on issues they do not hold ("worked on").
            *(
                fact
                for fact in tenant_changes
                if _fact_key(fact) not in held and fact.payload.get("author_member_id") == member_id
            ),
        ]
        return digest(
            member_id,
            since,
            until,
            issue_changes=_unique(changes),
            issue_facts=[fact for fact in task_facts if fact.source == ISSUE_FACT_SOURCE],
            vcs_facts=[fact for fact in own if fact.source in _GIT_SOURCES],
            task_updates=[fact for fact in task_facts if fact.source == TASK_UPDATE_FACT_SOURCE],
            assigned_keys=keys,
            mapping=await self.mapping(tenant_id),
            quiet_after_days=self._quiet_after_days,
        )

    async def assigned_keys(self, tenant_id: str, member_id: str, as_of: date) -> set[str]:
        """The tracker keys of the tasks the member holds on ``as_of``."""
        keys: set[str] = set()
        for edge in await self._graph.list_edges(
            tenant_id, from_node_id=member_id, kind=EdgeKind.ASSIGNED_TO
        ):
            if not edge.is_active_on(as_of):
                continue
            node = await self._graph.get_node(tenant_id, edge.to_node_id)
            if node is None or node.kind is not NodeKind.TASK:
                continue
            key = node.metadata.get("key")
            keys.add((key if isinstance(key, str) and key else node.id).upper())
        return keys


def previous_checkin_at(preference: EffectiveCheckInPreference, as_of: date) -> datetime:
    """The member's check-in time on their check-in day before ``as_of``, in UTC.

    The day before when they have no check-in days at all.
    """
    weekdays = set(preference.weekdays)
    day = as_of - timedelta(days=1)
    for _ in range(7):
        if not weekdays or day.weekday() in weekdays:
            break
        day -= timedelta(days=1)
    return checkin_time(preference, day)


def checkin_time(preference: EffectiveCheckInPreference, day: date) -> datetime:
    """When the member is asked on ``day``, in UTC."""
    zone = resolve_timezone(preference.timezone, "UTC")
    return datetime.combine(day, preference.local_time, tzinfo=zone).astimezone(UTC)


def day_start(preference: EffectiveCheckInPreference, day: date) -> datetime:
    """Midnight opening ``day`` in the member's time zone, in UTC."""
    zone = resolve_timezone(preference.timezone, "UTC")
    return datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)


def _last_answer_at(facts: Iterable[FactEvent], until: datetime) -> datetime | None:
    answered = [
        fact.observed_at
        for fact in facts
        if fact.source == _CHECKIN_FACT_SOURCE
        and fact.payload.get("status_source") in _ANSWER_SOURCES
        and fact.observed_at <= until
    ]
    return max(answered) if answered else None


def _fact_key(fact: FactEvent) -> str:
    key = fact.payload.get("key") or fact.payload.get("issue_key")
    return key.upper() if isinstance(key, str) and key else fact.entity_ref.id.upper()


def _unique(facts: Iterable[FactEvent]) -> list[FactEvent]:
    return list({fact.correlation_id: fact for fact in facts}.values())
