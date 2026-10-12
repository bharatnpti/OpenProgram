"""Why the date moved, for one scope over a period: the kept breakdowns summed and explained.

Each forecast_days row keeps why its forecast moved since the scope's day
before (core/domain/impact.py). A period ("since planning", "the last 7 days")
sums its rows, today's computed live as every read of today is. Around the
numbers it says:

- what kind of scope was added: a change request (the baseline's change label,
  tenant data), an addition without the label, a bug, or moved in from another
  release or project, with who brought it in (D14: the changelog author of the
  change that brought it into scope, else its creator or reporter);
- the two other dates: each move of the committed date with who, when and why,
  and the team's own latest date, before and after, with its cause;
- evidence, never a share: requirements open past their date, and the ones with
  no date at all;
- the timeline's markers for the period, and the link to its row. The timeline
  (core/application/timeline_service.py) reads the same markers from the kept
  rows alone, so Ora and the picture never disagree: scope added (with or
  without the change label), bugs, scope removed, reopened, closing late, the
  committed date's moves, and blockers opened and closed for the readers of the
  pod's blockers.

Who reads it is the delivery forecast's own rule, decided once here for the
route and for Ora alike (``access``). A developer reads none of it. A scrum
master reads the projects their pods work on and the pods they run in full,
and another pod's dates only. Executives never see who added an item. Nothing
ranks or counts people: "added by" sits on the item's own row.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from enum import StrEnum
from typing import Protocol

from core.application.authorization import AuthorizationPolicy, Capability
from core.application.baseline_service import BaselineService
from core.application.checkin_drift import CHECKIN_DRIFT_FACT_SOURCE, ETA_STATED
from core.application.delivery_scope import DeliveryScopeService, reads_project
from core.application.forecast_service import ForecastService, OpenRequirement
from core.application.person_names import UNKNOWN_PERSON, PersonNames
from core.application.sync_services import ISSUE_CHANGE_FACT_SOURCE
from core.domain.auth import Principal, Role
from core.domain.baseline import DEFAULT_CHANGE_LABEL, BaselineScopeKind, PlanningBaseline
from core.domain.delivery import DeliveryStage
from core.domain.errors import AuthorizationDenied, GraphNotFound
from core.domain.forecast import (
    CommitmentScope,
    CommitmentScopeKind,
    DateChange,
    ForecastDayRecord,
    ReleaseMatchKind,
    Verdict,
    split_names,
)
from core.domain.graph import EntityRef, FactEvent, GraphNode, NodeKind
from core.domain.impact import (
    Driver,
    Item,
    KeptDay,
    Measure,
    PeriodAttribution,
    PlanningRule,
    ScopeGroup,
    Shift,
    attribution_from_payload,
    key_order,
    scope_group,
    summed,
    working_days_moved,
)
from core.domain.impact_words import (
    DRIVER_LABELS,
    PACE,
    answer_line,
    day_line,
    group_words,
    measure_words,
    moved_words,
    throughput_words,
)
from core.ports.forecast import CommitmentRepository
from core.ports.repositories import GraphRepository, StatusRepository, TimeSeriesRepository

#: The items a driver, a marker or a list names; the rest are counted.
MAX_ITEMS = 10
#: The most days a period reads back.
MAX_PERIOD_DAYS = 90
#: A period with nothing to start from (no baseline, no date change) reads these days.
FALLBACK_DAYS = 30
#: A committed date moved this soon after scope was added is shown beside that addition.
COMMITTED_AFTER_ADDED_WORKING_DAYS = 2
#: A move of the 85% date this many working days later leads the day report's "Most important".
LEADS_WORKING_DAYS = 3
#: How far before a period's first day its first row may sit.
_ANCHOR_LOOKBACK_DAYS = 7
#: The most of a blocker's own words a marker's list repeats.
_WAITS_ON_CHARS = 120
#: The change that brings an issue into a project or pod: it becomes a requirement type.
_ENTERING_FIELDS = frozenset({"issue_type"})
#: Into a release, also the fix version or label the release is.
_RELEASE_FIELDS: Mapping[ReleaseMatchKind, str] = {
    ReleaseMatchKind.FIX_VERSION: "fix_version",
    ReleaseMatchKind.LABEL: "labels",
}
_VERDICT_ORDER: Mapping[Verdict, int] = {
    Verdict.ON_TRACK: 0,
    Verdict.AT_RISK: 1,
    Verdict.OFF_TRACK: 2,
}


class PeriodKind(StrEnum):
    SINCE_BASELINE = "since_baseline"
    LAST_7_DAYS = "last_7_days"
    LAST_30_DAYS = "last_30_days"
    SINCE_DATE_CHANGE = "since_date_change"
    #: From a day the reader names.
    FROM_DAY = "from"


@dataclass(frozen=True, kw_only=True)
class ImpactPeriod:
    kind: PeriodKind = PeriodKind.SINCE_BASELINE
    #: The first day, for FROM_DAY.
    from_day: date | None = None


class ImpactDetail(StrEnum):
    #: The drivers with their items, the evidence and the markers.
    FULL = "full"
    #: The dates only: how far the date moved, and by which kind of driver.
    DATES = "dates"


@dataclass(frozen=True, kw_only=True)
class ImpactAccess:
    detail: ImpactDetail
    #: Whether "added by" is named on an added item (D14: never for executives).
    added_by: bool
    #: Whether blockers opened and closed are marked: the pod blocker read's readers only.
    blockers: bool = False


class OpenFindings(Protocol):
    """The issues of a scope with an open Jira hygiene finding (lane F), for the ring on an
    addition without the change label. Until one is wired, no marker is ringed."""

    async def open_keys(self, tenant_id: str, scope: CommitmentScope) -> frozenset[str]: ...


@dataclass(frozen=True, kw_only=True)
class ImpactItemView:
    key: str
    title: str
    type: str | None
    #: The item's share of its driver, in working days, on the measure read.
    working_days: float
    #: The day it moved: joined, left or reopened.
    day: date | None
    group: ScopeGroup | None = None
    #: Whether it carries the change label (added items only).
    change_label: bool | None = None
    added_by: str | None = None


@dataclass(frozen=True, kw_only=True)
class ImpactDriverView:
    #: "pace", or a Driver's value other than time passed and work finished.
    kind: str
    #: Scope added is one driver per group.
    group: ScopeGroup | None
    #: Working days on the measure read; p50 and p85 both, for the toggle.
    working_days: float
    p50: float
    p85: float
    items: tuple[ImpactItemView, ...] = ()
    #: Items beyond those listed.
    more: int = 0
    note: str = ""


@dataclass(frozen=True, kw_only=True)
class ImpactEnd:
    day: date
    p50: date | None
    p85: date | None
    #: The date the verdict was read against that day: committed, else the Jira release date.
    committed: date | None
    verdict: Verdict | None = None


@dataclass(frozen=True, kw_only=True)
class CommittedChangeView:
    from_date: date | None
    to_date: date | None
    on: date
    by: str
    note: str
    #: Requirements added within two working days before the move.
    after_added: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class TeamMoveView:
    day: date
    from_date: date | None
    from_key: str | None
    to_date: date | None
    to_key: str | None
    #: eta_moved, due_moved, date_changed, added, finished, removed, overtaken or no_dates.
    cause: str
    words: str


@dataclass(frozen=True, kw_only=True)
class TeamDateView:
    from_date: date | None
    from_key: str | None
    to_date: date | None
    to_key: str | None
    moves: tuple[TeamMoveView, ...] = ()


@dataclass(frozen=True, kw_only=True)
class LateView:
    key: str
    title: str
    stage: DeliveryStage
    date: date
    #: due, eta or baseline_due: which date it is past.
    date_kind: str
    working_days_late: int


@dataclass(frozen=True, kw_only=True)
class EvidenceView:
    """What explains pace without a share of its own (D12)."""

    late: tuple[LateView, ...] = ()
    more_late: int = 0
    undated: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ScopeChangesView:
    """The scope ledger: what joined and left, whether or not the move was taken apart."""

    added: tuple[ImpactItemView, ...] = ()
    removed: tuple[ImpactItemView, ...] = ()
    reopened: tuple[ImpactItemView, ...] = ()
    added_count: int = 0
    removed_count: int = 0
    reopened_count: int = 0
    finished_count: int = 0


@dataclass(frozen=True, kw_only=True)
class MarkerItemView:
    key: str
    title: str
    stage: DeliveryStage | None
    group: ScopeGroup | None = None
    added_by: str | None = None
    #: Scope added: whether this item carries the change label.
    change_label: bool | None = None
    #: A blocker: what it waits on, in the words of the check-in that raised it.
    waits_on: str | None = None
    #: Scope added without the change label: whether a Jira hygiene finding is open on
    #: it. None until hygiene findings are wired (OpenFindings).
    hygiene: bool | None = None


@dataclass(frozen=True, kw_only=True)
class MarkerView:
    """One kind of event on one day, on the timeline's impact lane."""

    day: date
    #: scope_added, bug, scope_removed, reopened, late, blocker_opened, blocker_closed
    #: or committed_date_changed.
    kind: str
    count: int
    items: tuple[MarkerItemView, ...] = ()
    #: For scope added: whether the items carry the change label.
    change_label: bool | None = None


@dataclass(frozen=True, kw_only=True)
class ImpactView:
    scope: CommitmentScope
    name: str
    project_name: str
    period: PeriodKind
    #: "since planning (1 Oct)", "in the last 7 days".
    period_words: str
    measure: Measure
    detail: ImpactDetail
    change_label: str
    baseline_day: date | None
    start: ImpactEnd | None
    end: ImpactEnd | None
    moved_working_days: float | None
    unit: str | None
    #: The answer line, in words.
    summary: str
    drivers: tuple[ImpactDriverView, ...]
    scope_changes: ScopeChangesView
    evidence: EvidenceView | None
    committed_changes: tuple[CommittedChangeView, ...]
    team_date: TeamDateView | None
    markers: tuple[MarkerView, ...]
    link: str
    reconstructed_days: int
    not_known: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class ImpactLine:
    """The day report's "what changed" line, and whether it leads Most important."""

    text: str
    leads: bool


@dataclass(frozen=True, kw_only=True)
class _Period:
    start: date
    words: str
    notes: tuple[str, ...]
    baseline: PlanningBaseline | None


@dataclass(frozen=True, kw_only=True)
class _Added:
    """What is known of one added item beyond the breakdown: its group and who added it."""

    group: ScopeGroup
    change_label: bool
    added_by: str | None


@dataclass(frozen=True, kw_only=True)
class _PeriodData:
    rows: tuple[ForecastDayRecord, ...]
    period: PeriodAttribution | None
    rule: PlanningRule
    added: Mapping[str, _Added]


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class DeliveryImpactService:
    def __init__(
        self,
        *,
        forecast_service: ForecastService,
        baseline_service: BaselineService,
        delivery_scope_service: DeliveryScopeService,
        commitment_repository: CommitmentRepository,
        graph_repository: GraphRepository,
        time_series_repository: TimeSeriesRepository,
        status_repository: StatusRepository | None = None,
        open_findings: OpenFindings | None = None,
        clock: Callable[[], datetime] = _utc_now,
        today: Callable[[], date] | None = None,
        zone: tzinfo = UTC,
    ) -> None:
        self._forecasts = forecast_service
        self._baselines = baseline_service
        self._scope = delivery_scope_service
        self._commitments = commitment_repository
        self._graph = graph_repository
        self._facts = time_series_repository
        self._status = status_repository
        self._findings = open_findings
        self._today = today or (lambda: clock().date())
        self._zone = zone

    # ---- who reads what ----------------------------------------------------

    async def access(self, principal: Principal, scope: CommitmentScope) -> ImpactAccess:
        """What of the scope's breakdown the principal reads; AuthorizationDenied when nothing.

        A project or release: the delivery forecast's own rule (ensure_project_read).
        A pod: whole for a project's readers and for a scrum master who runs it,
        dates only for a scrum master's other pods, nothing for a developer.
        """
        policy = AuthorizationPolicy()
        names = any(principal.has_role(role) for role in (Role.PO, Role.MGR, Role.ADMIN, Role.SM))
        # Blockers are per person: the pod blocker read's readers (scrum master, manager,
        # admin), never a product owner or an executive.
        blockers = policy.can(principal, Capability.READ_POD_BLOCKERS)
        if scope.kind is not CommitmentScopeKind.POD:
            await self._scope.ensure_project_read(principal, scope.project_id)
            return ImpactAccess(detail=ImpactDetail.FULL, added_by=names, blockers=blockers)
        if reads_project(policy, principal, own=False):
            return ImpactAccess(detail=ImpactDetail.FULL, added_by=names, blockers=blockers)
        if principal.has_role(Role.SM):
            runs = await self._forecasts.runs_pod(
                principal.tenant_id, scope.id, principal.subject, self._today()
            )
            return ImpactAccess(
                detail=ImpactDetail.FULL if runs else ImpactDetail.DATES,
                added_by=runs,
                blockers=runs and blockers,
            )
        raise AuthorizationDenied("Not allowed.")

    async def check_scope(self, tenant_id: str, scope: CommitmentScope, as_of: date) -> None:
        """Raise GraphNotFound unless the scope is the project's (its pod, its release)."""
        if scope.kind is CommitmentScopeKind.RELEASE:
            release = await self._forecasts.release(tenant_id, scope.id)
            if release.project_id != scope.project_id:
                raise GraphNotFound(f"No release {scope.id!r} in project {scope.project_id!r}.")
            return
        if scope.kind is CommitmentScopeKind.POD:
            pods = await self._forecasts.pod_projects(tenant_id, scope.id, as_of)
            if scope.project_id not in {project.id for project in pods}:
                raise GraphNotFound(f"Pod {scope.id!r} does not work on {scope.project_id!r}.")
            return
        project = await self._graph.get_node(tenant_id, scope.project_id, as_of=as_of)
        if project is None or project.kind is not NodeKind.PROJECT:
            raise GraphNotFound(f"No project {scope.project_id!r}.")

    # ---- the read ------------------------------------------------------------

    async def impact(
        self,
        tenant_id: str,
        scope: CommitmentScope,
        *,
        period: ImpactPeriod,
        measure: Measure,
        as_of: date,
        access: ImpactAccess,
    ) -> ImpactView:
        """What moved the scope's forecast over the period up to ``as_of``, and why."""
        as_of = min(as_of, self._today())
        await self.check_scope(tenant_id, scope, as_of)
        name, project_name = await self._names(tenant_id, scope, as_of)
        resolved = await self._period(tenant_id, scope, period, as_of)
        data = await self._period_data(tenant_id, scope, resolved.start, as_of, access)
        full = access.detail is ImpactDetail.FULL
        attribution = data.period
        requirements = await self._forecasts.scope_requirements(tenant_id, scope, as_of)
        snapshot = await self._forecasts.scope_snapshot(tenant_id, scope, as_of)
        stages = dict(snapshot.items) if snapshot is not None else {}
        notes = list(resolved.notes)
        notes += _period_notes(attribution, requirements.view.history.reason)
        groups = {key: added.group for key, added in data.added.items()}
        committed = await self._committed_changes(tenant_id, scope, resolved.start, as_of, data)
        late = _late(requirements.open, as_of, resolved.baseline) if full else []
        summary = (
            answer_line(
                attribution,
                groups,
                measure=measure,
                change_label=data.rule.change_label,
                period_words=resolved.words,
            )
            if attribution is not None and full
            else _dates_summary(attribution, measure, resolved.words)
        )
        return ImpactView(
            scope=scope,
            name=name,
            project_name=project_name,
            period=period.kind,
            period_words=resolved.words,
            measure=measure,
            detail=access.detail,
            change_label=data.rule.change_label,
            baseline_day=resolved.baseline.planned_on if resolved.baseline else None,
            start=_end(data.rows, attribution, first=True),
            end=_end(data.rows, attribution, first=False),
            moved_working_days=(
                attribution.moved.of(measure)
                if attribution is not None and attribution.moved
                else None
            ),
            unit=attribution.unit if attribution is not None else None,
            summary=summary,
            drivers=_drivers(attribution, data, measure, full, late),
            scope_changes=_scope_changes(attribution, data, measure, full),
            evidence=(
                EvidenceView(
                    late=tuple(late[:MAX_ITEMS]),
                    more_late=max(0, len(late) - MAX_ITEMS),
                    undated=requirements.view.team.undated_keys,
                )
                if full
                else None
            ),
            committed_changes=committed,
            team_date=await self._team_date(tenant_id, data.rows),
            markers=(
                await self._markers(
                    tenant_id,
                    scope,
                    data,
                    committed,
                    late,
                    stages,
                    start=resolved.start,
                    as_of=as_of,
                    access=access,
                )
                if full and attribution is not None
                else ()
            ),
            link=(
                f"/reports/{scope.project_id}/overall?row={scope.kind.value}:{scope.id}#timeline"
            ),
            reconstructed_days=attribution.reconstructed_days if attribution is not None else 0,
            not_known=tuple(dict.fromkeys(notes)),
        )

    async def report_line(
        self, tenant_id: str, scope: CommitmentScope, *, since: date | None, day: date
    ) -> ImpactLine | None:
        """The day report's line when the 85% date moved since ``since`` (the previous
        report's snapshot day); None when it did not, or nothing says why."""
        if since is None or since >= day:
            return None
        data = await self._period_data(
            tenant_id, scope, since, day, ImpactAccess(detail=ImpactDetail.FULL, added_by=False)
        )
        attribution = data.period
        if attribution is None or attribution.moved is None:
            return None
        text = day_line(
            attribution,
            {key: added.group for key, added in data.added.items()},
            change_label=data.rule.change_label,
        )
        if text is None:
            return None
        first, last = data.rows[0].verdict, data.rows[-1].verdict
        worse = (
            first in _VERDICT_ORDER
            and last in _VERDICT_ORDER
            and _VERDICT_ORDER[last] > _VERDICT_ORDER[first]
        )
        # Most important is about what threatens the date: an earlier date never leads it.
        return ImpactLine(text=text, leads=worse or attribution.moved.p85 >= LEADS_WORKING_DAYS)

    # ---- the period ------------------------------------------------------------

    async def _period(
        self, tenant_id: str, scope: CommitmentScope, period: ImpactPeriod, as_of: date
    ) -> _Period:
        baseline = await self._baseline(tenant_id, scope, as_of)
        notes: list[str] = []
        start: date
        words: str
        if period.kind is PeriodKind.SINCE_BASELINE:
            if baseline is not None:
                start = baseline.planned_on
                words = f"since planning ({_short(start)})"
            else:
                start = as_of - timedelta(days=FALLBACK_DAYS)
                words = f"in the last {FALLBACK_DAYS} days"
                notes.append(
                    "Planning is not marked complete yet, so this covers the last "
                    f"{FALLBACK_DAYS} days."
                )
        elif period.kind is PeriodKind.SINCE_DATE_CHANGE:
            changed = await self._last_date_change(tenant_id, scope, as_of)
            if changed is not None:
                start = changed
                words = f"since the date last changed ({_short(start)})"
            else:
                start = as_of - timedelta(days=FALLBACK_DAYS)
                words = f"in the last {FALLBACK_DAYS} days"
                notes.append(
                    f"No delivery date was committed yet, so this covers the last "
                    f"{FALLBACK_DAYS} days."
                )
        elif period.kind is PeriodKind.FROM_DAY and period.from_day is not None:
            start = period.from_day
            words = f"since {_short(start)}"
        else:
            days = 7 if period.kind is PeriodKind.LAST_7_DAYS else 30
            start = as_of - timedelta(days=days)
            words = f"in the last {days} days"
        earliest = as_of - timedelta(days=MAX_PERIOD_DAYS)
        if start < earliest:
            notes.append(f"Only the last {MAX_PERIOD_DAYS} days are read.")
            start = earliest
        return _Period(start=min(start, as_of), words=words, notes=tuple(notes), baseline=baseline)

    async def _baseline(
        self, tenant_id: str, scope: CommitmentScope, day: date
    ) -> PlanningBaseline | None:
        """The release's own baseline, else the project's: a pod reads its project's."""
        if scope.kind is CommitmentScopeKind.RELEASE:
            own = await self._baselines.baseline_on(
                tenant_id, BaselineScopeKind.RELEASE, scope.id, day
            )
            if own is not None:
                return own
        return await self._baselines.baseline_on(
            tenant_id, BaselineScopeKind.PROJECT, scope.project_id, day
        )

    async def _last_date_change(
        self, tenant_id: str, scope: CommitmentScope, as_of: date
    ) -> date | None:
        changes = [
            change
            for change in await self._commitments.changes(tenant_id, scope)
            if self._local_day(change.changed_at) <= as_of
        ]
        return self._local_day(changes[-1].changed_at) if changes else None

    async def _period_data(
        self,
        tenant_id: str,
        scope: CommitmentScope,
        start: date,
        as_of: date,
        access: ImpactAccess,
        *,
        live: bool = True,
    ) -> _PeriodData:
        rows = await self._rows(tenant_id, scope, start, as_of, live=live)
        attribution = (
            summed(
                [
                    KeptDay(
                        day=row.day,
                        p50=row.p50,
                        p85=row.p85,
                        attribution=attribution_from_payload(row.attribution),
                        reconstructed=row.reconstructed,
                    )
                    for row in rows
                ]
            )
            if rows
            else None
        )
        baseline = await self._baseline(tenant_id, scope, as_of)
        rule = (
            PlanningRule(
                change_label=baseline.change_label,
                planned_on=baseline.planned_on,
                grace_working_days=baseline.grace_working_days,
                baseline_keys=baseline.keys,
            )
            if baseline is not None
            else PlanningRule(change_label=DEFAULT_CHANGE_LABEL)
        )
        added = (
            await self._added(
                tenant_id,
                attribution.added,
                rule,
                access,
                await self._forecasts.tasks_by_key(tenant_id, scope.project_id, as_of),
                await self._entering_fields(tenant_id, scope),
            )
            if attribution is not None
            else {}
        )
        return _PeriodData(rows=tuple(rows), period=attribution, rule=rule, added=added)

    async def _rows(
        self,
        tenant_id: str,
        scope: CommitmentScope,
        start: date,
        as_of: date,
        *,
        live: bool = True,
    ) -> list[ForecastDayRecord]:
        """The period's first row (the last on or before ``start``) and every row after it.

        Today's is computed now, as every read of today is, unless ``live`` is False:
        the timeline reads the kept rows only, today's as the hourly run last kept it.
        """
        kept = await self._forecasts.kept_days(
            tenant_id, scope, start - timedelta(days=_ANCHOR_LOOKBACK_DAYS), as_of
        )
        if live and as_of >= self._today():
            today = await self._forecasts.forecast_day(tenant_id, scope, as_of)
            kept = [row for row in kept if row.day < as_of] + ([today] if today else [])
        before = [row for row in kept if row.day <= start]
        anchor = before[-1] if before else (kept[0] if kept else None)
        if anchor is None:
            return []
        return [anchor, *(row for row in kept if row.day > anchor.day)]

    async def _added(
        self,
        tenant_id: str,
        items: Sequence[Item],
        rule: PlanningRule,
        access: ImpactAccess,
        tasks: Mapping[str, GraphNode],
        entering: frozenset[str],
    ) -> dict[str, _Added]:
        """Each added item's group and, where the reader may see it, who brought it in."""
        found: dict[str, _Added] = {}
        authors: dict[str, str | None] = {}
        for item in items:
            task = tasks.get(item.key) or await self._graph.get_node(tenant_id, item.key)
            if task is not None and task.kind is not NodeKind.TASK:
                task = None
            metadata = task.metadata if task is not None else {}
            labels = split_names(metadata.get("labels"))
            created = _created_on(metadata.get("created_at"), self._zone)
            since = item.since or item.day
            existed = created is not None and since is not None and created < since
            joined = item.day or since or self._today()
            group = scope_group(
                key=item.key,
                issue_type=item.type or _text(metadata.get("issue_type")),
                labels=labels,
                joined_on=joined,
                existed_before=existed,
                rule=rule,
            )
            wanted = rule.change_label.casefold()
            authors[item.key] = (
                await self._author(tenant_id, item, metadata, created, entering)
                if access.added_by
                else None
            )
            found[item.key] = _Added(
                group=group,
                change_label=any(label.casefold() == wanted for label in labels),
                added_by=None,
            )
        if not access.added_by:
            return found
        names = await PersonNames(self._graph).resolve(
            tenant_id, {author for author in authors.values() if author}
        )
        return {
            key: _Added(
                group=added.group,
                change_label=added.change_label,
                added_by=(names.get(author) or UNKNOWN_PERSON)
                if (author := authors.get(key))
                else None,
            )
            for key, added in found.items()
        }

    async def _author(
        self,
        tenant_id: str,
        item: Item,
        metadata: Mapping[str, object],
        created: date | None,
        entering: frozenset[str],
    ) -> str | None:
        """Who brought the item into scope (D14): the author of the change that did,
        else its creator when it was created then, else its reporter. A member id."""
        since, day = item.since, item.day
        if day is not None:
            first = self._start_of(since or day - timedelta(days=_ANCHOR_LOOKBACK_DAYS))
            last = self._start_of(day + timedelta(days=1))
            brought_in = [
                fact
                for fact in await self._facts.list_facts(
                    tenant_id, EntityRef(tenant_id=tenant_id, kind=NodeKind.TASK, id=item.key)
                )
                if fact.source == ISSUE_CHANGE_FACT_SOURCE
                and fact.payload.get("field") in entering
                and fact.payload.get("to")
                and first <= fact.observed_at < last
            ]
            for fact in sorted(brought_in, key=lambda fact: fact.observed_at, reverse=True):
                author = fact.payload.get("author_member_id")
                if isinstance(author, str) and author:
                    return author
            if created is not None and (since is None or created >= since):
                creator = metadata.get("creator_member_id")
                if isinstance(creator, str) and creator:
                    return creator
        reporter = metadata.get("reporter_member_id")
        return reporter if isinstance(reporter, str) and reporter else None

    # ---- the timeline's markers ---------------------------------------------------

    async def timeline_markers(
        self,
        tenant_id: str,
        scope: CommitmentScope,
        *,
        start: date,
        as_of: date,
        access: ImpactAccess,
        open_requirements: Sequence[OpenRequirement],
        baseline: PlanningBaseline | None,
        stages: Mapping[str, DeliveryStage],
    ) -> tuple[MarkerView, ...]:
        """One timeline row's impact lane after ``start`` up to ``as_of``, from the kept
        rows alone (no forecast is run): the markers the impact read carries, so the
        picture and Ora say the same events."""
        data = await self._period_data(tenant_id, scope, start, as_of, access, live=False)
        committed = await self._committed_changes(tenant_id, scope, start, as_of, data)
        return await self._markers(
            tenant_id,
            scope,
            data,
            committed,
            _late(open_requirements, as_of, baseline),
            stages,
            start=start,
            as_of=as_of,
            access=access,
        )

    async def _markers(
        self,
        tenant_id: str,
        scope: CommitmentScope,
        data: _PeriodData,
        committed: Sequence[CommittedChangeView],
        late: Sequence[LateView],
        stages: Mapping[str, DeliveryStage],
        *,
        start: date,
        as_of: date,
        access: ImpactAccess,
    ) -> tuple[MarkerView, ...]:
        """The timeline's impact lane: one marker per day and kind, never per person.

        Each is an event on work items: scope added (with or without the change
        label, ringed where a hygiene finding is open on it), a bug opened in the
        scope (counted as a requirement or not), scope removed, reopened, a
        requirement passing its date while open, the committed date's moves, and,
        for the pod blocker read's readers only, blockers opened and closed on the
        scope's requirements.
        """
        grouped: dict[tuple[date, str, bool | None], list[MarkerItemView]] = {}

        def add(
            day: date | None, kind: str, item: MarkerItemView, label: bool | None = None
        ) -> None:
            if day is not None and start < day <= as_of:
                grouped.setdefault((day, kind, label), []).append(item)

        flagged = (
            await self._findings.open_keys(tenant_id, scope) if self._findings is not None else None
        )
        if data.period is not None:
            _attribution_markers(data.period, data, stages, flagged, add)
        for entry in late:
            add(
                entry.date + timedelta(days=1),
                "late",
                MarkerItemView(key=entry.key, title=entry.title, stage=entry.stage),
            )
        # Bugs that are not counted as requirements: the forecast never sees them, so
        # the day each was opened is all the timeline says.
        for key, task in (await self._forecasts.uncounted_bugs(tenant_id, scope, as_of)).items():
            add(
                _created_on(task.metadata.get("created_at"), self._zone),
                "bug",
                MarkerItemView(key=key, title=task.name, stage=None, group=ScopeGroup.BUG),
            )
        if access.blockers and self._status is not None:
            await self._blocker_markers(tenant_id, scope, stages, start, as_of, add)
        markers = [
            MarkerView(
                day=day,
                kind=kind,
                count=len(items),
                items=tuple(sorted(items, key=lambda item: key_order(item.key)))[:MAX_ITEMS],
                change_label=label,
            )
            for (day, kind, label), items in grouped.items()
        ]
        moved: dict[date, int] = {}
        for change in committed:
            if start < change.on <= as_of:
                moved[change.on] = moved.get(change.on, 0) + 1
        markers += [
            MarkerView(day=day, kind="committed_date_changed", count=count)
            for day, count in moved.items()
        ]
        return tuple(sorted(markers, key=lambda marker: (marker.day, marker.kind)))

    async def _blocker_markers(
        self,
        tenant_id: str,
        scope: CommitmentScope,
        stages: Mapping[str, DeliveryStage],
        start: date,
        as_of: date,
        add: _Add,
    ) -> None:
        """Blockers opened and closed on the scope's requirements, on the day each was."""
        assert self._status is not None
        tasks = await self._forecasts.tasks_by_key(tenant_id, scope.project_id, as_of)
        # A blocker names its work item by the task's node id, or (as seeded and as an
        # attribution reads it) by its issue key: either finds the requirement.
        keys_by_id = {
            ref: key
            for key, task in tasks.items()
            if key in stages
            for ref in dict.fromkeys((task.id, key))
        }
        for blocker in await self._status.blockers_on_work_items(
            tenant_id, sorted(keys_by_id), start, as_of
        ):
            key = keys_by_id.get(blocker.work_item_id or "")
            if key is None:
                continue
            item = MarkerItemView(
                key=key,
                title=tasks[key].name,
                stage=stages.get(key),
                waits_on=_clip(blocker.description),
            )
            add(blocker.first_seen_on, "blocker_opened", item)
            add(blocker.resolved_on, "blocker_closed", item)

    # ---- the other two dates ---------------------------------------------------------

    async def _entering_fields(self, tenant_id: str, scope: CommitmentScope) -> frozenset[str]:
        """The changelog fields whose change brings an issue into the scope."""
        if scope.kind is not CommitmentScopeKind.RELEASE:
            return _ENTERING_FIELDS
        release = await self._forecasts.release(tenant_id, scope.id)
        return _ENTERING_FIELDS | {_RELEASE_FIELDS[release.match.kind]}

    async def _committed_changes(
        self,
        tenant_id: str,
        scope: CommitmentScope,
        start: date,
        as_of: date,
        data: _PeriodData,
    ) -> tuple[CommittedChangeView, ...]:
        """Each move of the committed date in the period: who, when, why, and the additions
        it came right after."""
        changes = await self._commitments.changes(tenant_id, scope)
        names = await PersonNames(self._graph).resolve(
            tenant_id, {change.changed_by for change in changes}
        )
        added = data.period.added if data.period is not None else ()
        listed: list[CommittedChangeView] = []
        previous: DateChange | None = None
        for change in changes:
            on = self._local_day(change.changed_at)
            if start <= on <= as_of:
                listed.append(
                    CommittedChangeView(
                        from_date=previous.target_date if previous is not None else None,
                        to_date=change.target_date,
                        on=on,
                        by=names.get(change.changed_by) or UNKNOWN_PERSON,
                        note=change.note,
                        after_added=tuple(
                            item.key
                            for item in added
                            if item.day is not None
                            and 0
                            <= working_days_moved(item.day, on)
                            <= COMMITTED_AFTER_ADDED_WORKING_DAYS
                        ),
                    )
                )
            previous = change
        return tuple(listed)

    async def _team_date(
        self, tenant_id: str, rows: Sequence[ForecastDayRecord]
    ) -> TeamDateView | None:
        """The team's latest date at both ends, and each day it moved, with its cause."""
        if not rows:
            return None
        moves: list[TeamMoveView] = []
        etas: list[FactEvent] | None = None
        for previous, row in zip(rows, rows[1:], strict=False):
            if (previous.team_latest, previous.team_latest_key) == (
                row.team_latest,
                row.team_latest_key,
            ):
                continue
            if etas is None:
                etas = await self._facts.list_recent_facts(
                    tenant_id, sources=(CHECKIN_DRIFT_FACT_SOURCE,), limit=5000
                )
            moves.append(await self._team_move(tenant_id, previous, row, etas))
        first, last = rows[0], rows[-1]
        return TeamDateView(
            from_date=first.team_latest,
            from_key=first.team_latest_key,
            to_date=last.team_latest,
            to_key=last.team_latest_key,
            moves=tuple(moves[-5:]),
        )

    async def _team_move(
        self,
        tenant_id: str,
        previous: ForecastDayRecord,
        row: ForecastDayRecord,
        etas: Sequence[FactEvent],
    ) -> TeamMoveView:
        attribution = attribution_from_payload(row.attribution)
        added = {item.key for item in attribution.added} if attribution else set()
        finished = {item.key for item in attribution.finished} if attribution else set()
        removed = {item.key for item in attribution.removed} if attribution else set()
        old_key, new_key = previous.team_latest_key, row.team_latest_key
        cause: str
        if row.team_latest is None:
            cause = "no_dates"
            words = "No open requirement has an ETA or a due date any more."
        elif old_key == new_key and new_key is not None:
            stated = self._stated_on(etas, new_key, previous.day, row.day)
            if stated is not None:
                cause = "eta_moved"
                words = (
                    f"{new_key}'s ETA moved {_short(previous.team_latest)} → "
                    f"{_short(row.team_latest)} (stated {_short(stated)})."
                )
            elif await self._due_moved(tenant_id, new_key, previous.day, row.day):
                cause = "due_moved"
                words = (
                    f"{new_key}'s due date moved {_short(previous.team_latest)} → "
                    f"{_short(row.team_latest)}."
                )
            else:
                cause = "date_changed"
                words = (
                    f"{new_key}'s date moved {_short(previous.team_latest)} → "
                    f"{_short(row.team_latest)}."
                )
        elif new_key in added:
            cause = "added"
            words = f"{new_key} was added with a date of {_short(row.team_latest)}."
        elif old_key is not None and old_key in finished:
            cause = "finished"
            words = (
                f"{old_key} finished, so {new_key} ({_short(row.team_latest)}) is now the latest."
            )
        elif old_key is not None and old_key in removed:
            cause = "removed"
            words = (
                f"{old_key} left the scope, so {new_key} ({_short(row.team_latest)}) is now "
                "the latest."
            )
        else:
            cause = "overtaken"
            words = f"{new_key}'s date ({_short(row.team_latest)}) is now the latest."
        return TeamMoveView(
            day=row.day,
            from_date=previous.team_latest,
            from_key=old_key,
            to_date=row.team_latest,
            to_key=new_key,
            cause=cause,
            words=words,
        )

    def _stated_on(
        self, facts: Sequence[FactEvent], key: str, after: date, until: date
    ) -> date | None:
        """The day the latest ETA for ``key`` was stated between the two days, if one was."""
        first, last = (
            self._start_of(after + timedelta(days=1)),
            self._start_of(until + timedelta(days=1)),
        )
        stated = [
            fact.observed_at
            for fact in facts
            if fact.payload.get("kind") == ETA_STATED
            and fact.payload.get("issue_key") == key
            and first <= fact.observed_at < last
        ]
        return self._local_day(max(stated)) if stated else None

    async def _due_moved(self, tenant_id: str, key: str, after: date, until: date) -> bool:
        first, last = (
            self._start_of(after + timedelta(days=1)),
            self._start_of(until + timedelta(days=1)),
        )
        return any(
            fact.source == ISSUE_CHANGE_FACT_SOURCE
            and fact.payload.get("field") == "due_date"
            and first <= fact.observed_at < last
            for fact in await self._facts.list_facts(
                tenant_id, EntityRef(tenant_id=tenant_id, kind=NodeKind.TASK, id=key)
            )
        )

    # ---- lookups -----------------------------------------------------------------

    async def _names(self, tenant_id: str, scope: CommitmentScope, as_of: date) -> tuple[str, str]:
        project = await self._graph.get_node(tenant_id, scope.project_id, as_of=as_of)
        project_name = project.name if project is not None else scope.project_id
        if scope.kind is CommitmentScopeKind.PROJECT:
            return project_name, project_name
        if scope.kind is CommitmentScopeKind.RELEASE:
            release = await self._forecasts.release(tenant_id, scope.id)
            return release.name, project_name
        pod = await self._graph.get_node(tenant_id, scope.id, as_of=as_of)
        return (_name(pod) or scope.id), project_name

    def _local_day(self, moment: datetime) -> date:
        return moment.astimezone(self._zone).date() if moment.tzinfo else moment.date()

    def _start_of(self, day: date) -> datetime:
        return datetime.combine(day, time(), tzinfo=self._zone)


# ---- assembling the view -------------------------------------------------------------


def _end(
    rows: Sequence[ForecastDayRecord], attribution: PeriodAttribution | None, *, first: bool
) -> ImpactEnd | None:
    if not rows:
        return None
    if first and attribution is not None:
        row = next((row for row in rows if row.day == attribution.start), rows[0])
    else:
        row = rows[0] if first else rows[-1]
    return ImpactEnd(
        day=row.day, p50=row.p50, p85=row.p85, committed=row.target_date, verdict=row.verdict
    )


def _period_notes(attribution: PeriodAttribution | None, reason: str | None) -> list[str]:
    if attribution is None:
        return ["No forecast was kept for this period yet."]
    notes: list[str] = []
    if attribution.to_p85 is None:
        notes.append(f"No forecast yet: {reason}" if reason else "No forecast yet.")
    if attribution.forecast_began is not None:
        notes.append(
            f"The forecast had too little history before {_short(attribution.forecast_began)}; "
            "the move is read from that day."
        )
    if attribution.unknown_days:
        days = ", ".join(_short(day) for day in attribution.unknown_days[:5])
        more = len(attribution.unknown_days) - 5
        notes.append(
            "Why the forecast moved is not known for "
            + (f"{days} and {more} more days" if more > 0 else days)
            + ": no breakdown was kept."
        )
    return notes


def _dates_summary(attribution: PeriodAttribution | None, measure: Measure, words: str) -> str:
    if attribution is None or attribution.moved is None:
        return f"No forecast to compare {words}."
    return f"The {measure_words(measure)} {moved_words(attribution.moved.of(measure))} {words}."


def _drivers(
    attribution: PeriodAttribution | None,
    data: _PeriodData,
    measure: Measure,
    full: bool,
    late: Sequence[LateView],
) -> tuple[ImpactDriverView, ...]:
    """The waterfall's bars: pace, scope added by group, and every other driver; worst first."""
    if attribution is None or attribution.moved is None:
        return ()
    found: list[ImpactDriverView] = []
    pace = attribution.driver(Driver.TIME_PASSED) + attribution.driver(Driver.WORK_FINISHED)
    if not _negligible(pace, measure):
        note = f"{len(attribution.finished)} finished"
        if late:
            note += "; " + ", ".join(item.key for item in late[:3]) + " late"
        found.append(
            _driver_view(
                PACE,
                None,
                pace,
                measure,
                _items(attribution.finished, data, measure) if full else (),
                note if full else "pace",
                len(attribution.finished),
            )
        )
    found += _added_drivers(attribution, data, measure, full)
    for driver, items in (
        (Driver.SCOPE_REMOVED, attribution.removed),
        (Driver.REOPENED, attribution.reopened),
    ):
        shift = attribution.driver(driver)
        if _negligible(shift, measure) and not items:
            continue
        found.append(
            _driver_view(
                driver.value,
                None,
                shift,
                measure,
                _items(items, data, measure) if full else (),
                f"{len(items)} {_noun(len(items))} {DRIVER_LABELS[driver].split()[-1]}"
                if full
                else DRIVER_LABELS[driver],
                len(items),
            )
        )
    for driver in (Driver.RE_ESTIMATED, Driver.THROUGHPUT, Driver.ROUNDING, Driver.NOT_ATTRIBUTED):
        shift = attribution.driver(driver)
        if _negligible(shift, measure):
            continue
        note = throughput_words(attribution) if driver is Driver.THROUGHPUT and full else ""
        found.append(
            _driver_view(driver.value, None, shift, measure, (), note or DRIVER_LABELS[driver], 0)
        )
    return tuple(sorted(found, key=lambda view: -view.working_days))


def _added_drivers(
    attribution: PeriodAttribution, data: _PeriodData, measure: Measure, full: bool
) -> list[ImpactDriverView]:
    """Scope added, one bar per group: change requests, without the label, bugs, moved in."""
    by_group: dict[ScopeGroup, list[Item]] = {}
    for item in attribution.added:
        added = data.added.get(item.key)
        group = added.group if added is not None else ScopeGroup.BEFORE_PLANNING
        by_group.setdefault(group, []).append(item)
    found: list[ImpactDriverView] = []
    for group in ScopeGroup:
        items = by_group.get(group, [])
        if not items:
            continue
        shift = Shift(
            p50=round(sum(item.share.p50 for item in items), 2),
            p85=round(sum(item.share.p85 for item in items), 2),
        )
        words = group_words(group, data.rule.change_label, len(items))
        found.append(
            _driver_view(
                Driver.SCOPE_ADDED.value,
                group,
                shift,
                measure,
                _items(items, data, measure) if full else (),
                f"{len(items)} {_noun(len(items))} added, {words}"
                if full
                else DRIVER_LABELS[Driver.SCOPE_ADDED],
                len(items),
            )
        )
    return found


def _driver_view(
    kind: str,
    group: ScopeGroup | None,
    shift: Shift,
    measure: Measure,
    items: tuple[ImpactItemView, ...],
    note: str,
    count: int,
) -> ImpactDriverView:
    return ImpactDriverView(
        kind=kind,
        group=group,
        working_days=round(shift.of(measure), 2),
        p50=round(shift.p50, 2),
        p85=round(shift.p85, 2),
        items=items[:MAX_ITEMS],
        more=max(0, count - MAX_ITEMS) if items else 0,
        note=note,
    )


def _items(
    items: Iterable[Item], data: _PeriodData, measure: Measure
) -> tuple[ImpactItemView, ...]:
    return tuple(
        ImpactItemView(
            key=item.key,
            title=item.title,
            type=item.type,
            working_days=round(item.share.of(measure), 2),
            day=item.day,
            group=added.group if (added := data.added.get(item.key)) else None,
            change_label=added.change_label if added else None,
            added_by=added.added_by if added else None,
        )
        for item in sorted(items, key=lambda item: key_order(item.key))
    )


def _scope_changes(
    attribution: PeriodAttribution | None, data: _PeriodData, measure: Measure, full: bool
) -> ScopeChangesView:
    if attribution is None:
        return ScopeChangesView()
    return ScopeChangesView(
        added=_items(attribution.added, data, measure)[:MAX_ITEMS] if full else (),
        removed=_items(attribution.removed, data, measure)[:MAX_ITEMS] if full else (),
        reopened=_items(attribution.reopened, data, measure)[:MAX_ITEMS] if full else (),
        added_count=len(attribution.added),
        removed_count=len(attribution.removed),
        reopened_count=len(attribution.reopened),
        finished_count=len(attribution.finished),
    )


def _late(
    requirements: Sequence[OpenRequirement], as_of: date, baseline: PlanningBaseline | None
) -> list[LateView]:
    """Open requirements past their due date, else their ETA, else their baseline due date."""
    found: list[LateView] = []
    for requirement in requirements:
        planned = baseline.items.get(requirement.key) if baseline is not None else None
        for kind, day in (
            ("due", requirement.due),
            ("eta", requirement.eta),
            ("baseline_due", planned.due if planned is not None else None),
        ):
            if day is not None and day < as_of:
                found.append(
                    LateView(
                        key=requirement.key,
                        title=requirement.title,
                        stage=requirement.stage,
                        date=day,
                        date_kind=kind,
                        working_days_late=max(0, working_days_moved(day, as_of)),
                    )
                )
                break
    return sorted(found, key=lambda item: (-item.working_days_late, key_order(item.key)))


_Add = Callable[..., None]


def _attribution_markers(
    attribution: PeriodAttribution,
    data: _PeriodData,
    stages: Mapping[str, DeliveryStage],
    flagged: frozenset[str] | None,
    add: _Add,
) -> None:
    """The kept breakdowns' events: scope added (a bug apart), removed and reopened."""
    for item in attribution.added:
        added = data.added.get(item.key)
        group = added.group if added is not None else None
        added_by = added.added_by if added is not None else None
        if group is ScopeGroup.BUG:
            add(
                item.day,
                "bug",
                MarkerItemView(
                    key=item.key,
                    title=item.title,
                    stage=stages.get(item.key),
                    group=group,
                    added_by=added_by,
                ),
            )
            continue
        labelled = added.change_label if added is not None else False
        add(
            item.day,
            "scope_added",
            MarkerItemView(
                key=item.key,
                title=item.title,
                stage=stages.get(item.key),
                group=group,
                added_by=added_by,
                change_label=labelled,
                hygiene=(item.key in flagged) if flagged is not None and not labelled else None,
            ),
            labelled,
        )
    for kind, items in (("scope_removed", attribution.removed), ("reopened", attribution.reopened)):
        for item in items:
            add(
                item.day,
                kind,
                MarkerItemView(key=item.key, title=item.title, stage=stages.get(item.key)),
            )


def _clip(text: str) -> str | None:
    """A blocker's words, kept short: what it waits on, never more than a line."""
    words = " ".join(text.split())
    if not words:
        return None
    return words if len(words) <= _WAITS_ON_CHARS else words[: _WAITS_ON_CHARS - 1].rstrip() + "…"


def _negligible(shift: Shift, measure: Measure) -> bool:
    """A bar of no working days on the measure read is left out."""
    return abs(shift.of(measure)) < 0.05


def _noun(count: int) -> str:
    return "requirement" if count == 1 else "requirements"


def _short(day: date | None) -> str:
    return f"{day.day} {day:%b}" if day is not None else "no date"


def _name(node: GraphNode | None) -> str | None:
    return node.name if node is not None else None


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _created_on(value: object, zone: tzinfo) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        created = datetime.fromisoformat(value)
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return created.astimezone(zone).date() if created.tzinfo else created.date()
