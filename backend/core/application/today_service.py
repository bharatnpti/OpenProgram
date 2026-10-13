"""The person's own reads behind Today and Check-in: their conversation, who must act, counts.

- ``own_conversation``: the person's own chat turns of one day, read without
  refreshing their retention (D87, D95). Nobody reads another person's turns
  here, admins included: the reader is always the caller.
- ``project_asks``: a project's "who must act", the day report's asks
  (``DayReportBuilder.asks``) as one flat list ordered by the item, never by
  person. An executive gets the owning team instead of anyone's name, and no
  wording that names a person (D87, R5).
- ``today_counts``: the replies of the day ("8 of 14 replied, 3 filled from
  Jira") and the open warnings, over the part of the portfolio the caller
  reads. A developer reads no aggregate.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from core.application.day_report_asks import Ask, AskScope, Team
from core.application.day_report_builder import DayReportBuilder
from core.application.delivery_scope import DeliveryScopeService, MemberScope
from core.application.risk_service import RiskService
from core.domain.auth import Principal, Role
from core.domain.conversation import ConversationRole
from core.domain.errors import AuthorizationDenied
from core.domain.escalation_matrix import NEED_LABELS, NeedType
from core.domain.graph import EdgeKind, GraphEdge, GraphNode, NodeKind
from core.domain.status import StatusSource
from core.ports.repositories import ConversationRepository, GraphRepository, StatusRepository

#: How a person's own turns are kept: "not kept after 30 days" past it.
DEFAULT_RETENTION_DAYS = 30
NO_AGGREGATE = "Counts of the day are read by the people who run the teams."
#: Stands in for a person's name in an executive's wording.
SOMEONE = "a team member"
_REPLIED_SOURCES = frozenset({StatusSource.CONFIRMED, StatusSource.PARTIAL})
_NAME_READERS = (Role.ADMIN, Role.MGR, Role.PO, Role.SM)
_PORTFOLIO_READERS = (Role.ADMIN, Role.MGR, Role.EXEC)

CountsScope = Literal["portfolio", "own"]


# ---- the person's own conversation ---------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ConversationTurnView:
    role: Literal["agent", "user"]
    text: str
    at: datetime


@dataclass(frozen=True, kw_only=True)
class ConversationDayView:
    """One day of the person's own conversation, or why it is not shown.

    ``kept`` False: the day is past the retention window, so its turns are no
    longer kept (and are not looked for).
    """

    day: date
    kept: bool
    retention_days: int
    turns: tuple[ConversationTurnView, ...] = ()


# ---- who must act ----------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ProjectAskView:
    """One ask: what is needed, who can do it, how long it waited, why it matters."""

    need: NeedType
    need_label: str
    what: str
    #: The person who can do it; for an executive, the owning team instead.
    who: str | None
    team: str | None
    waited_days: int | None
    #: A fact about the item ("reported by Asha Rao, waits on Payments Pod"); none for an exec.
    detail: str | None
    issue_key: str | None
    level: int
    escalated_to: str | None
    escalation_label: str | None
    #: The request between people it is, when it is one (see ``Ask.request_id``).
    request_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class ProjectAsksView:
    project_id: str
    project_name: str
    as_of: date
    names_hidden: bool
    asks: tuple[ProjectAskView, ...]


# ---- counts ----------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class RepliesCount:
    """The day's replies over the people counted: answered, filled from the tracker, not yet."""

    people: int
    replied: int
    inferred: int
    waiting: int


@dataclass(frozen=True, kw_only=True)
class TodayCountsView:
    as_of: date
    scope: CountsScope
    replies: RepliesCount
    open_warnings: int
    project_ids: tuple[str, ...]


def reads_names(principal: Principal) -> bool:
    """Everyone but an executive reads who owns an ask; an executive reads the team."""
    return any(principal.has_role(role) for role in _NAME_READERS)


def reads_portfolio(principal: Principal) -> bool:
    return any(principal.has_role(role) for role in _PORTFOLIO_READERS)


class TodayService:
    def __init__(
        self,
        *,
        conversation_repository: ConversationRepository,
        status_repository: StatusRepository,
        graph_repository: GraphRepository,
        day_reports: DayReportBuilder,
        delivery_scope: DeliveryScopeService,
        risk_service: RiskService,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._conversations = conversation_repository
        self._status = status_repository
        self._graph = graph_repository
        self._day_reports = day_reports
        self._scope = delivery_scope
        self._risks = risk_service
        self._retention_days = retention_days
        self._clock = clock or (lambda: datetime.now(tz=UTC))

    async def own_conversation(
        self, tenant_id: str, developer_id: str, day: date
    ) -> ConversationDayView:
        """The person's own turns of ``day``, oldest first, without touching their retention."""
        today = self._clock().date()
        if day < today - timedelta(days=self._retention_days):
            return ConversationDayView(day=day, kept=False, retention_days=self._retention_days)
        turns = await self._conversations.peek_turns_for_day(tenant_id, developer_id, day)
        return ConversationDayView(
            day=day,
            kept=True,
            retention_days=self._retention_days,
            turns=tuple(
                ConversationTurnView(
                    role="user" if turn.role is ConversationRole.USER else "agent",
                    text=turn.content,
                    at=turn.observed_at,
                )
                for turn in turns
                if turn.developer_id == developer_id and turn.content.strip()
            ),
        )

    async def project_asks(
        self, principal: Principal, project_id: str, day: date
    ) -> ProjectAsksView:
        """The project's "who must act" for ``day``, for a reader of its progress."""
        await self._scope.ensure_project_read(principal, project_id)
        found = await self._day_reports.asks(principal.tenant_id, project_id, day)
        hide = not reads_names(principal)
        scope = found.scope
        return ProjectAsksView(
            project_id=scope.project.id,
            project_name=scope.project.name,
            as_of=day,
            names_hidden=hide,
            asks=tuple(_ask_view(ask, scope, hide=hide) for ask in found.asks),
        )

    async def today_counts(self, principal: Principal, day: date) -> TodayCountsView:
        """The day's replies and open warnings over the part of the portfolio the caller reads.

        Managers, executives and admins count every pod's people and every
        project; a scrum master their own pods' people and those pods'
        projects; a product owner their own projects and the people in them.
        A developer reads no aggregate.
        """
        tenant_id = principal.tenant_id
        portfolio = reads_portfolio(principal)
        if not portfolio and not (principal.has_role(Role.SM) or principal.has_role(Role.PO)):
            raise AuthorizationDenied(NO_AGGREGATE)
        nodes = await self._graph.list_nodes(tenant_id, as_of=day)
        by_id = {node.id: node for node in nodes}
        contains = [
            edge
            for edge in await self._graph.list_edges(tenant_id, kind=EdgeKind.CONTAINS)
            if edge.is_active_on(day)
        ]
        if portfolio:
            projects = sorted(node.id for node in nodes if node.kind is NodeKind.PROJECT)
            pods = {node.id for node in nodes if node.kind is NodeKind.POD}
        else:
            scope = await self._scope.scope_of(tenant_id, principal.subject)
            projects, pods = _own_part(principal, scope, contains, by_id)
        people = sorted(
            {
                edge.to_node_id
                for edge in contains
                if edge.from_node_id in pods
                and (node := by_id.get(edge.to_node_id)) is not None
                and node.kind is NodeKind.DEVELOPER
            }
        )
        replies = await self._replies(tenant_id, people, day)
        warnings = await self._open_warnings(tenant_id, projects, day, portfolio=portfolio)
        return TodayCountsView(
            as_of=day,
            scope="portfolio" if portfolio else "own",
            replies=replies,
            open_warnings=warnings,
            project_ids=tuple(projects),
        )

    async def _replies(self, tenant_id: str, people: Sequence[str], day: date) -> RepliesCount:
        """Replied (confirmed or partial) and inferred today, as the pod coverage counts them."""
        replied = inferred = 0
        for person in people:
            status = await self._status.latest_developer_status(tenant_id, person, day)
            if status is None or status.as_of != day:
                continue
            if status.source in _REPLIED_SOURCES:
                replied += 1
            elif status.source is StatusSource.INFERRED:
                inferred += 1
        return RepliesCount(
            people=len(people),
            replied=replied,
            inferred=inferred,
            waiting=len(people) - replied - inferred,
        )

    async def _open_warnings(
        self, tenant_id: str, projects: Sequence[str], day: date, *, portfolio: bool
    ) -> int:
        """Open risks and drift findings, as Early warnings lists them."""
        if portfolio:
            return len(await self._risks.portfolio_risks(tenant_id, day)) + len(
                await self._risks.portfolio_drift(tenant_id, day)
            )
        total = 0
        for project_id in projects:
            total += len(await self._risks.project_risks(tenant_id, project_id, day))
            total += len(await self._risks.project_drift(tenant_id, project_id, day))
        return total


def _own_part(
    principal: Principal,
    scope: MemberScope,
    contains: Iterable[GraphEdge],
    by_id: Mapping[str, GraphNode],
) -> tuple[list[str], set[str]]:
    """A scrum master's and a product owner's part: their projects, and the pods counted.

    A scrum master counts their own pods, a product owner the pods of their own
    projects; someone with both counts both.
    """
    pods: set[str] = set(scope.pods) if principal.has_role(Role.SM) else set()
    if principal.has_role(Role.PO):
        pods |= {
            edge.to_node_id
            for edge in contains
            if edge.from_node_id in scope.projects
            and (node := by_id.get(edge.to_node_id)) is not None
            and node.kind is NodeKind.POD
        }
    return sorted(scope.projects), pods


def _ask_view(ask: Ask, scope: AskScope, *, hide: bool) -> ProjectAskView:
    if not hide:
        return ProjectAskView(
            need=ask.need,
            need_label=NEED_LABELS[ask.need],
            what=ask.text,
            who=ask.owner,
            team=ask.team.node.name if ask.team is not None else None,
            waited_days=ask.waited_days,
            detail=_detail(ask),
            issue_key=ask.issue_key,
            level=ask.level,
            escalated_to=ask.escalated_to,
            escalation_label=ask.escalation_label,
            request_id=ask.request_id,
        )
    return ProjectAskView(
        need=ask.need,
        need_label=NEED_LABELS[ask.need],
        what=without_names(ask.text, scope.names.values()),
        who=_team_label(ask.team, scope),
        team=ask.team.node.name if ask.team is not None else None,
        waited_days=ask.waited_days,
        detail=None,
        issue_key=ask.issue_key,
        level=ask.level,
        escalated_to=None,
        escalation_label=ask.escalation_label,
        request_id=ask.request_id,
    )


def _detail(ask: Ask) -> str | None:
    """The ask's fact, unless it only says again who acts ("reported by" its own owner)."""
    if not ask.detail or (ask.owner and ask.detail == f"reported by {ask.owner}"):
        return None
    return ask.detail


def _team_label(team: Team | None, scope: AskScope) -> str:
    """The owning team, as an executive reads an ask's who: the pod, else the project."""
    return team.node.name if team is not None else scope.project.name


def without_names(text: str, names: Iterable[str]) -> str:
    """``text`` with every member's name, and their first name, replaced by "a team member".

    Longest names first, so "Asha Rao" goes whole before "Asha" could. Issue
    keys and every other word stay as they are.
    """
    words: set[str] = set()
    for name in names:
        clean = " ".join(name.split())
        if not clean:
            continue
        words.add(clean)
        first = clean.split(" ")[0]
        if len(first) >= 3:
            words.add(first)
    result = text
    for word in sorted(words, key=len, reverse=True):
        result = re.sub(rf"(?<![\w-]){re.escape(word)}(?:'s)?(?![\w-])", SOMEONE, result)
    return result
