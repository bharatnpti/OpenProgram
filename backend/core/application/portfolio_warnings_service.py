"""Early warnings across the portfolio, grouped by kind: what Jira and Git say before people do.

One read for every project a person reads, in groups with a count each:

- **blockers people reported**, with their age and who resolves them: the
  assignee of the issue a cross-team blocker waits on, else that team's scrum
  master; a blocker within the team goes to its scrum master (the day report's
  rule, ``day_report_asks.blocker_ask``). Listed to the pods' blocker readers
  (a manager, an admin, a scrum master for the pods they run); everyone else
  reads the count;
- **says on track, tools disagree**: the drift where a stated status and the
  tracker or Git disagree, worded as the item disagreeing; the owner is named
  only to themself, anyone else reads the team;
- **done in code, open in Jira**: merged, and the issue still open;
- **review stuck**: a merge request open past its rule's days;
- **no movement** and **work with no code yet**: the other risk rules.

Findings attach to work items: groups and counts are by kind, items by age,
never by person. An executive reads no person: the team stands for the owner.
A drift finding's age counts from when the scheduled scan first saw it
(``RiskService``); until the scan has recorded long enough, an age that starts
on its first recording is a "seen since" day, never a guess.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from zoneinfo import ZoneInfo

from core.application.authorization import AuthorizationPolicy, Capability
from core.application.blocker_resolution import BlockerResolutionService, ResolvedBlocker
from core.application.delivery_scope import DeliveryScopeService
from core.application.portfolio_index import IndexedProject, PortfolioIndex, PortfolioIndexReader
from core.application.portfolio_privacy import reads_people
from core.application.risk_service import RiskService
from core.domain.auth import Principal, Role
from core.domain.graph import EdgeKind, EntityRef, NodeKind
from core.domain.risk import DriftFinding, DriftFindingKind, RiskFinding, RiskRuleId
from core.domain.rollup import Rag
from core.domain.status import local_date
from core.ports.repositories import GraphRepository

#: The most of a blocker's own words an item repeats.
MAX_LINE = 140


class WarningKind(StrEnum):
    BLOCKERS = "blockers"
    TOOLS_DISAGREE = "tools_disagree"
    DONE_IN_CODE = "done_in_code"
    REVIEW_STUCK = "review_stuck"
    NO_MOVEMENT = "no_movement"
    NO_CODE_YET = "no_code_yet"


_DISAGREE = frozenset(
    {
        DriftFindingKind.GREEN_OVER_RED,
        DriftFindingKind.CLAIMED_PROGRESS_NO_ACTIVITY,
        DriftFindingKind.SAID_DONE_NO_PR,
        DriftFindingKind.SAID_IN_REVIEW_NO_MR,
        DriftFindingKind.ETA_DISAGREEMENT,
    }
)
_RISK_GROUPS: dict[RiskRuleId, WarningKind] = {
    RiskRuleId.PR_AGE: WarningKind.REVIEW_STUCK,
    RiskRuleId.STALE_WORK_ITEM: WarningKind.NO_MOVEMENT,
    RiskRuleId.FEATURE_NO_PR: WarningKind.NO_CODE_YET,
}


@dataclass(frozen=True, kw_only=True)
class WarningItem:
    id: str
    #: The drift kind, the risk rule, or "blocker".
    kind: str
    key: str
    line: str
    age_days: int
    #: When it was first seen; None when not known.
    since: date | None
    #: False when the age is only "seen since" the first recording.
    age_known: bool
    #: Who acts on it, by name; None where the reader sees the team instead.
    owner: str | None
    owner_team: str | None
    #: The reader is the owner.
    owner_is_you: bool
    project_id: str
    project_name: str
    severity: Rag
    evidence_url: str | None = None
    #: The owner's own status says on track while the signals disagree.
    watermelon: bool = False


@dataclass(frozen=True, kw_only=True)
class WarningGroup:
    kind: WarningKind
    title: str
    count: int
    #: The items, oldest first; empty for a reader who reads only the count.
    items: tuple[WarningItem, ...]
    #: The reader reads the count only (blockers, for a reader of no pod's blockers).
    counts_only: bool = False


@dataclass(frozen=True, kw_only=True)
class PortfolioWarningsView:
    as_of: date
    #: The first day a drift finding's first sight was recorded: an age starting on it
    #: reads "seen since". None until the scan has recorded one.
    seen_since: date | None
    groups: tuple[WarningGroup, ...]
    total: int
    projects: int


class PortfolioWarningsService:
    def __init__(
        self,
        *,
        index: PortfolioIndexReader,
        risks: RiskService,
        blockers: BlockerResolutionService,
        graph: GraphRepository,
        scope: DeliveryScopeService,
        tracker: str = "the issue tracker",
        vcs: str = "Git",
        policy: AuthorizationPolicy | None = None,
        zone: ZoneInfo | None = None,
    ) -> None:
        self._index = index
        self._risks = risks
        self._blockers = blockers
        self._graph = graph
        self._scope = scope
        self._tracker = tracker
        self._vcs = vcs
        self._policy = policy or AuthorizationPolicy()
        # A finding's "since" is the day it was detected in the tenant's zone, the
        # calendar ``as_of`` is on; the router passes ServiceRegistry.tenant_zone.
        self._zone = zone or ZoneInfo("UTC")

    async def read(
        self,
        principal: Principal,
        as_of: date,
        *,
        project_id: str | None = None,
        search: str = "",
    ) -> PortfolioWarningsView:
        tenant_id = principal.tenant_id
        index = await self._index.read(principal, as_of)
        projects = [project for project in index.projects if project.matches(search)]
        if project_id:
            projects = [project for project in projects if project.id == project_id]
        reader = _Reader(
            subject=principal.subject,
            named=reads_people(principal),
            index=index,
            seen_since=await self._risks.drift_seen_since(tenant_id, as_of),
            as_of=as_of,
        )
        items: dict[WarningKind, list[WarningItem]] = {kind: [] for kind in WarningKind}
        drift = await self._risks.drift_by_project(
            tenant_id, [project.id for project in projects], as_of
        )
        risks = await self._risks.risks_by_project(tenant_id, as_of)
        for project in projects:
            for finding in drift.get(project.id, []):
                kind = _drift_group(finding.kind)
                items[kind].append(self._drift_item(finding, project, reader, kind))
            for risk in risks.get(project.id, []):
                kind = _RISK_GROUPS.get(risk.rule_id, WarningKind.NO_MOVEMENT)
                items[kind].append(_risk_item(risk, project, reader, self._zone))
        blockers = await self._blocker_items(principal, projects, reader)
        items[WarningKind.BLOCKERS] = list(blockers.listed)
        groups = tuple(
            WarningGroup(
                kind=kind,
                title=self._title(kind),
                count=blockers.count if kind is WarningKind.BLOCKERS else len(_unique(found)),
                items=tuple(sorted(_unique(found), key=_oldest_first)),
                counts_only=kind is WarningKind.BLOCKERS and blockers.counts_only,
            )
            for kind, found in items.items()
        )
        return PortfolioWarningsView(
            as_of=as_of,
            seen_since=reader.seen_since,
            groups=groups,
            total=sum(group.count for group in groups),
            projects=len(projects),
        )

    def _title(self, kind: WarningKind) -> str:
        return {
            WarningKind.BLOCKERS: "Blockers people reported",
            WarningKind.TOOLS_DISAGREE: "Says on track, tools disagree",
            WarningKind.DONE_IN_CODE: f"Done in code, open in {self._tracker}",
            WarningKind.REVIEW_STUCK: "Review stuck",
            WarningKind.NO_MOVEMENT: "No movement",
            WarningKind.NO_CODE_YET: "Work with no code yet",
        }[kind]

    def _drift_item(
        self, finding: DriftFinding, project: IndexedProject, reader: _Reader, kind: WarningKind
    ) -> WarningItem:
        key = reader.label(finding.entity_ref)
        since = local_date(finding.detected_at, self._zone)
        # Worded as the item disagreeing; only its owner sees their own name (P5).
        disagree = kind is WarningKind.TOOLS_DISAGREE
        owner = reader.owner(finding.owner_id, project, finding.entity_ref, hide=disagree)
        return WarningItem(
            id=f"drift:{finding.kind.value}:{project.id}:{finding.entity_ref.id}",
            kind=finding.kind.value,
            key=key,
            line=self._drift_line(finding, key),
            age_days=max(0, (reader.as_of - since).days),
            since=since,
            age_known=reader.known(since),
            owner=owner.name,
            owner_team=owner.team,
            owner_is_you=owner.is_you,
            project_id=project.id,
            project_name=project.name,
            severity=finding.severity,
            evidence_url=finding.evidence.url if finding.evidence else None,
        )

    def _drift_line(self, finding: DriftFinding, key: str) -> str:
        words = {
            DriftFindingKind.SAID_DONE_NO_PR: f"{key} is marked done with no merge request.",
            DriftFindingKind.CLAIMED_PROGRESS_NO_ACTIVITY: (
                f"{key} is reported in progress with no {self._vcs} activity."
            ),
            DriftFindingKind.SAID_IN_REVIEW_NO_MR: (
                f"{key} is said to be in review, but no open merge request names it."
            ),
            DriftFindingKind.ETA_DISAGREEMENT: f"The ETAs given for {key} do not overlap.",
            DriftFindingKind.MERGED_ISSUE_OPEN: (
                f"{key} was merged but is still open in {self._tracker}."
            ),
        }.get(finding.kind)
        return words or _trimmed(finding.reason)

    async def _blocker_items(
        self, principal: Principal, projects: Sequence[IndexedProject], reader: _Reader
    ) -> _Blockers:
        """Each project's open blockers: all counted, listed for the pods the reader reads.

        Read for everyone, so a reader who sees only the count gets the right one;
        listed only for the pods whose blockers the reader reads.
        """
        tenant_id = principal.tenant_id
        listed_pods = await self._blocker_pods(principal)
        assignees: dict[str, list[str]] = {}
        for edge in await self._graph.list_edges(tenant_id, kind=EdgeKind.ASSIGNED_TO):
            if edge.is_active_on(reader.as_of):
                assignees.setdefault(edge.to_node_id, []).append(edge.from_node_id)
        found: list[WarningItem] = []
        count = 0
        cache: dict[str, tuple[ResolvedBlocker, ...]] = {}
        for project in projects:
            pods = {pod.id for pod in project.pods}
            seen: set[str] = set()
            for person in reader.index.people_of(project):
                if person not in cache:
                    cache[person] = await self._blockers.open_blockers_for_developer(
                        tenant_id, person, reader.as_of
                    )
                for blocker in cache[person]:
                    here = [pod for pod in blocker.pod_ids if pod in pods]
                    if not here or blocker.blocker_id in seen:
                        continue
                    seen.add(blocker.blocker_id)
                    count += 1
                    if listed_pods is not None and not set(here) & listed_pods:
                        continue
                    found.append(_blocker_item(blocker, project, here[0], reader, assignees))
        return _Blockers(listed=tuple(found), count=count, counts_only=listed_pods == frozenset())

    async def _blocker_pods(self, principal: Principal) -> frozenset[str] | None:
        """The pods whose blockers the reader may see listed: None for every pod."""
        if not self._policy.can(principal, Capability.READ_POD_BLOCKERS):
            return frozenset()
        if self._policy.can(principal, Capability.READ_PROGRAM_ROLLUP) or principal.has_role(
            Role.ADMIN
        ):
            return None
        scope = await self._scope.scope_of(principal.tenant_id, principal.subject)
        return scope.pods


@dataclass(frozen=True, kw_only=True)
class _Blockers:
    listed: tuple[WarningItem, ...]
    #: Every open blocker of the projects, listed or not.
    count: int
    #: The reader reads no pod's blockers: the count is all they get.
    counts_only: bool


@dataclass(frozen=True, kw_only=True)
class _Owner:
    name: str | None
    team: str | None
    is_you: bool


@dataclass(frozen=True, kw_only=True)
class _Reader:
    subject: str
    #: Whether the reader sees people by name (not an executive).
    named: bool
    index: PortfolioIndex
    seen_since: date | None
    as_of: date

    def known(self, since: date) -> bool:
        """Whether an age from ``since`` is the real one, not a "seen since"."""
        return self.seen_since is not None and since > self.seen_since

    def label(self, ref: EntityRef) -> str:
        node = self.index.nodes.get(ref.id)
        if node is None:
            return ref.id
        key = node.metadata.get("key")
        if isinstance(key, str) and key.strip():
            return key.strip()
        return ref.id if node.kind is NodeKind.TASK else node.name

    def team(self, person_id: str | None, project: IndexedProject) -> str | None:
        if person_id is None:
            return None
        pods = {pod.id for pod in project.pods}
        mine = self.index.pods_of_person(person_id)
        own = [pod for pod in mine if pod.id in pods] or list(mine)
        return own[0].name if own else None

    def owner(
        self,
        person_id: str | None,
        project: IndexedProject,
        ref: EntityRef | None = None,
        *,
        hide: bool = False,
    ) -> _Owner:
        team = self.team(person_id, project)
        if team is None and ref is not None:
            issue_pods = self.index.pods_of_issue(self.label(ref), project)
            team = issue_pods[0].name if issue_pods else None
        is_you = person_id is not None and person_id == self.subject
        named = self.named and (not hide or is_you)
        return _Owner(
            name=self.index.name(person_id) if named and person_id else None,
            team=team,
            is_you=is_you,
        )


def _drift_group(kind: DriftFindingKind) -> WarningKind:
    if kind is DriftFindingKind.MERGED_ISSUE_OPEN:
        return WarningKind.DONE_IN_CODE
    return WarningKind.TOOLS_DISAGREE if kind in _DISAGREE else WarningKind.NO_MOVEMENT


def _risk_item(
    risk: RiskFinding, project: IndexedProject, reader: _Reader, zone: ZoneInfo
) -> WarningItem:
    key = (
        risk.evidence.identifier
        if risk.entity_ref.kind is NodeKind.DEVELOPER
        else reader.label(risk.entity_ref)
    )
    owner = reader.owner(risk.owner_id, project, risk.entity_ref)
    since = local_date(risk.detected_at, zone)
    return WarningItem(
        id=f"risk:{risk.rule_id.value}:{project.id}:{risk.entity_ref.id}:{risk.evidence.identifier}",
        kind=risk.rule_id.value,
        key=key,
        line=_trimmed(risk.reason),
        age_days=risk.age_days,
        since=since,
        age_known=True,
        owner=owner.name,
        owner_team=owner.team,
        owner_is_you=owner.is_you,
        project_id=project.id,
        project_name=project.name,
        severity=risk.severity,
        evidence_url=risk.evidence.url,
        watermelon=risk.is_watermelon,
    )


def _blocker_item(
    blocker: ResolvedBlocker,
    project: IndexedProject,
    pod_id: str,
    reader: _Reader,
    assignees: Mapping[str, Sequence[str]],
) -> WarningItem:
    """A blocker with who resolves it: the waited issue's assignee, else a scrum master."""
    resolver: str | None
    if blocker.cross_team:
        waited = blocker.depends_on_ref.id if blocker.depends_on_ref is not None else None
        people = sorted(assignees.get(waited, ())) if waited else []
        waited_pod = blocker.depends_on_pod_ids[0] if blocker.depends_on_pod_ids else None
        resolver = (
            people[0]
            if people
            else (reader.index.scrum_master_of(waited_pod) if waited_pod else None)
        )
        team = reader.index.name(waited_pod)
        waits = ", ".join(blocker.depends_on_pod_names) or "another team"
        line = f"{_trimmed(blocker.description)} · waits on {waits}"
    else:
        resolver = reader.index.scrum_master_of(pod_id) or blocker.developer_id
        team = reader.index.name(pod_id)
        line = _trimmed(blocker.description)
    key = (
        reader.label(blocker.work_item_ref)
        if blocker.work_item_ref is not None
        else reader.index.name(pod_id) or "—"
    )
    owner = reader.owner(resolver, project)
    return WarningItem(
        id=f"blocker:{project.id}:{blocker.blocker_id}",
        kind="blocker",
        key=key,
        line=line,
        age_days=max(0, (reader.as_of - blocker.first_seen_on).days),
        since=blocker.first_seen_on,
        age_known=True,
        owner=owner.name,
        owner_team=team or owner.team,
        owner_is_you=owner.is_you,
        project_id=project.id,
        project_name=project.name,
        severity=Rag.RED if blocker.critical else Rag.AMBER,
    )


def _unique(items: Sequence[WarningItem]) -> list[WarningItem]:
    return list({item.id: item for item in items}.values())


def _oldest_first(item: WarningItem) -> tuple[object, ...]:
    return (-item.age_days, item.project_name.casefold(), item.key, item.id)


def _trimmed(text: str) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= MAX_LINE else clean[: MAX_LINE - 1].rstrip() + "…"


__all__ = [
    "PortfolioWarningsService",
    "PortfolioWarningsView",
    "WarningGroup",
    "WarningItem",
    "WarningKind",
]
