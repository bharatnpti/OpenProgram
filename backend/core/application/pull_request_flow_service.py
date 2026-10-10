"""Flow through review: how long pull and merge requests spend coding, waiting and in review.

Reads the synced ``vcs_pull_request`` facts, the latest one per request, for a
window of days ending on the day viewed. Merged requests in the window give the
stage times (p50 and p75); open ones are counted in the stage they are in now.
No workstreams are needed: the scope is the tenant, a program's projects, a
project's repositories, or a pod's members and repositories.

Descriptive only, like the work-item flow: nothing here scores or raises a risk.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from core.application.merge_request_links import (
    MERGE_REQUEST_FACT_SOURCE,
    is_merged_merge_request,
    is_open_merge_request,
)
from core.domain.errors import GraphNotFound, OpenProgramError
from core.domain.graph import EdgeKind, FactEvent, GraphEdge, GraphNode, JsonScalar, NodeKind
from core.domain.review_flow import (
    REQUEST_TYPE_LABELS,
    REQUEST_TYPE_ORDER,
    REVIEW_STAGE_LABELS,
    REVIEW_STAGE_ORDER,
    RequestType,
    ReviewStage,
    ReviewTimeline,
    TypeSource,
    classify_request,
    current_stage,
    labels_from_payload,
    payload_datetime,
    percentile,
    stage_hours,
    timeline_from_payload,
)
from core.ports.repositories import GraphRepository, TimeSeriesRepository

#: Open requests last touched this many days before the window starts still
#: count; older ones are not read (a request untouched that long is abandoned).
OPEN_LOOKBACK_DAYS = 90
FACT_SCAN_LIMIT = 30000
#: The requests listed one by one; the counts and stage times cover them all.
MERGED_ITEM_LIMIT = 400
OPEN_ITEM_LIMIT = 200


class FlowScopeInvalid(OpenProgramError):
    """More than one of program, project and pod was asked for."""


@dataclass(frozen=True, kw_only=True)
class FlowScopeView:
    #: "tenant", "program", "project" or "pod".
    kind: str
    id: str | None
    name: str | None
    #: The repositories read; None for every repository the tenant syncs.
    repos: tuple[str, ...] | None
    #: A pod's members whose requests count; None when authors are not narrowed.
    member_count: int | None


@dataclass(frozen=True, kw_only=True)
class StageFlowView:
    stage: ReviewStage
    label: str
    p50_hours: float | None
    p75_hours: float | None
    #: Merged requests in the window whose time in this stage is known.
    measured_count: int
    #: Open requests in this stage now.
    open_count: int


@dataclass(frozen=True, kw_only=True)
class RequestTypeCountView:
    request_type: RequestType
    label: str
    merged_count: int
    open_count: int


@dataclass(frozen=True, kw_only=True)
class PullRequestFlowItemView:
    repo: str
    number: str
    title: str
    web_url: str | None
    author_name: str | None
    request_type: RequestType
    type_source: TypeSource
    type_evidence: str | None
    #: "open" or "merged".
    state: str
    draft: bool
    #: The stage an open request is in now; None for a merged one or when not known.
    stage: ReviewStage | None
    stage_since: datetime | None
    stage_age_hours: float | None
    #: Hours in each stage the request has finished; None for one not finished or not known.
    stage_hours: Mapping[ReviewStage, float | None]
    opened_at: datetime | None
    merged_at: datetime | None
    reviewer_count: int
    #: Whether the request's history was read (its stage times can be known).
    timed: bool


@dataclass(frozen=True, kw_only=True)
class PullRequestFlowView:
    as_of: date
    window_days: int
    window_start: datetime
    window_end: datetime
    scope: FlowScopeView
    merged_count: int
    open_count: int
    #: Merged requests whose history was read, so their stages can be timed.
    timed_merged_count: int
    #: Merged with no review by anyone but the author.
    unreviewed_merged_count: int
    #: Requests synced before histories were read: their stages are not known.
    untimed_count: int
    stages: tuple[StageFlowView, ...]
    worst_jam_p50: ReviewStage | None
    worst_jam_p75: ReviewStage | None
    type_counts: tuple[RequestTypeCountView, ...]
    items: tuple[PullRequestFlowItemView, ...]
    items_truncated: bool
    #: What the figures leave out, in words.
    notes: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class _Scope:
    view: FlowScopeView
    repos: frozenset[str] | None
    members: frozenset[str] | None
    empty_reason: str | None = None


class PullRequestFlowService:
    def __init__(
        self,
        graph_repository: GraphRepository,
        time_series_repository: TimeSeriesRepository,
    ) -> None:
        self._graph_repository = graph_repository
        self._time_series_repository = time_series_repository

    async def flow(
        self,
        tenant_id: str,
        as_of: date,
        *,
        days: int = 30,
        program_id: str | None = None,
        project_id: str | None = None,
        pod_id: str | None = None,
        now: datetime | None = None,
    ) -> PullRequestFlowView:
        current = now or datetime.now(tz=UTC)
        # Today's read runs to now; a past day's to the end of that day.
        window_end = (
            current
            if as_of >= current.date()
            else datetime.combine(as_of + timedelta(days=1), time.min, tzinfo=UTC)
        )
        window_start = window_end - timedelta(days=days)
        nodes = await self._graph_repository.list_nodes(tenant_id, as_of=as_of)
        edges = [
            edge
            for edge in await self._graph_repository.list_edges(tenant_id, kind=EdgeKind.CONTAINS)
            if edge.is_active_on(as_of)
        ]
        scope = _resolve_scope(
            nodes, edges, program_id=program_id, project_id=project_id, pod_id=pod_id
        )
        facts = await self._time_series_repository.list_recent_facts(
            tenant_id,
            since=window_start - timedelta(days=OPEN_LOOKBACK_DAYS),
            sources=(MERGE_REQUEST_FACT_SOURCE,),
            limit=FACT_SCAN_LIMIT,
        )
        names = {node.id: node.name for node in nodes if node.kind is NodeKind.DEVELOPER}
        issue_types = _issue_types(nodes)
        merged: list[PullRequestFlowItemView] = []
        open_now: list[PullRequestFlowItemView] = []
        unreviewed = 0
        no_commit = 0
        for fact in _latest_per_request(facts, until=window_end):
            if not _in_scope(fact, scope):
                continue
            item = _item(fact, names=names, issue_types=issue_types, window_end=window_end)
            if item is None:
                continue
            if item.state == "merged":
                if item.merged_at is None or item.merged_at < window_start:
                    continue
                merged.append(item)
                if item.timed and item.stage_hours[ReviewStage.AWAITING_REVIEW] is None:
                    unreviewed += 1
            else:
                open_now.append(item)
            still_coding = item.stage is ReviewStage.CODING
            if item.timed and not still_coding and item.stage_hours[ReviewStage.CODING] is None:
                no_commit += 1
        stages = tuple(_stage_view(stage, merged, open_now) for stage in REVIEW_STAGE_ORDER)
        merged.sort(key=lambda item: item.merged_at or window_start, reverse=True)
        open_now.sort(key=lambda item: item.stage_age_hours or 0.0, reverse=True)
        untimed = sum(1 for item in (*merged, *open_now) if not item.timed)
        timed_merged = sum(1 for item in merged if item.timed)
        return PullRequestFlowView(
            as_of=as_of,
            window_days=days,
            window_start=window_start,
            window_end=window_end,
            scope=scope.view,
            merged_count=len(merged),
            open_count=len(open_now),
            timed_merged_count=timed_merged,
            unreviewed_merged_count=unreviewed,
            untimed_count=untimed,
            stages=stages,
            worst_jam_p50=_worst(stages, "p50_hours"),
            worst_jam_p75=_worst(stages, "p75_hours"),
            type_counts=_type_counts(merged, open_now),
            items=(*merged[:MERGED_ITEM_LIMIT], *open_now[:OPEN_ITEM_LIMIT]),
            items_truncated=len(merged) > MERGED_ITEM_LIMIT or len(open_now) > OPEN_ITEM_LIMIT,
            notes=_notes(scope, untimed=untimed, unreviewed=unreviewed, no_commit=no_commit),
        )


def _resolve_scope(
    nodes: Sequence[GraphNode],
    edges: Sequence[GraphEdge],
    *,
    program_id: str | None,
    project_id: str | None,
    pod_id: str | None,
) -> _Scope:
    asked = [
        (kind, node_id)
        for kind, node_id in (
            (NodeKind.PROGRAM, program_id),
            (NodeKind.PROJECT, project_id),
            (NodeKind.POD, pod_id),
        )
        if node_id
    ]
    if len(asked) > 1:
        raise FlowScopeInvalid("ask for one of program_id, project_id or pod_id, not several")
    if not asked:
        return _Scope(
            view=FlowScopeView(kind="tenant", id=None, name=None, repos=None, member_count=None),
            repos=None,
            members=None,
        )
    kind, node_id = asked[0]
    hierarchy = _Hierarchy(nodes, edges)
    node = hierarchy.by_id.get(node_id)
    if node is None or node.kind is not kind:
        raise GraphNotFound(f"{kind.value} {node_id} not found")
    if kind is NodeKind.POD:
        return _pod_scope(node, hierarchy)
    if kind is NodeKind.PROJECT:
        repos = hierarchy.project_repos(node)
    else:
        projects = hierarchy.children(node.id, NodeKind.PROJECT)
        repos = set().union(*(hierarchy.project_repos(project) for project in projects))
    return _Scope(
        view=_scope_view(node, repos=frozenset(repos), members=None),
        repos=frozenset(repos),
        members=None,
        empty_reason=None
        if repos
        else (
            f"This {kind.value} lists no repositories, so no requests are in it. "
            "An admin adds them in Admin › Entities."
        ),
    )


def _pod_scope(pod: GraphNode, hierarchy: _Hierarchy) -> _Scope:
    """A pod's members' requests, in the repositories it lists, else in those of
    its projects, else in any repository. A pod nobody is in is its repositories."""
    projects = hierarchy.parents(pod.id, NodeKind.PROJECT)
    repos = set(_repo_list(pod)) or set().union(
        *(hierarchy.project_repos(project) for project in projects)
    )
    members = frozenset(member.id for member in hierarchy.children(pod.id, NodeKind.DEVELOPER))
    narrowed = frozenset(repos) if repos or not members else None
    return _Scope(
        view=_scope_view(pod, repos=narrowed, members=members or None),
        repos=narrowed,
        members=members or None,
        empty_reason=None
        if repos or members
        else "This pod has no members and lists no repositories, so no requests are in it.",
    )


def _scope_view(
    node: GraphNode, *, repos: frozenset[str] | None, members: frozenset[str] | None
) -> FlowScopeView:
    return FlowScopeView(
        kind=node.kind.value,
        id=node.id,
        name=node.name,
        repos=tuple(sorted(repos)) if repos is not None else None,
        member_count=len(members) if members is not None else None,
    )


class _Hierarchy:
    """Who contains whom on the day read: programs, projects, pods and members."""

    def __init__(self, nodes: Sequence[GraphNode], edges: Sequence[GraphEdge]) -> None:
        self.by_id = {node.id: node for node in nodes}
        self._edges = edges

    def children(self, parent_id: str, kind: NodeKind) -> list[GraphNode]:
        return [
            self.by_id[edge.to_node_id]
            for edge in self._edges
            if edge.from_node_id == parent_id
            and edge.to_node_id in self.by_id
            and self.by_id[edge.to_node_id].kind is kind
        ]

    def parents(self, child_id: str, kind: NodeKind) -> list[GraphNode]:
        return [
            self.by_id[edge.from_node_id]
            for edge in self._edges
            if edge.to_node_id == child_id
            and edge.from_node_id in self.by_id
            and self.by_id[edge.from_node_id].kind is kind
        ]

    def project_repos(self, project: GraphNode) -> set[str]:
        """The repositories a project lists, and those its pods list."""
        repos = set(_repo_list(project))
        for pod in self.children(project.id, NodeKind.POD):
            repos.update(_repo_list(pod))
        return repos


def _repo_list(node: GraphNode) -> list[str]:
    value = node.metadata.get("github_repos")
    if not isinstance(value, str):
        return []
    return [item.strip() for item in value.replace("\n", ",").split(",") if item.strip()]


def _issue_types(nodes: Iterable[GraphNode]) -> dict[str, str]:
    """Each synced issue's tracker type, by upper-cased key."""
    types: dict[str, str] = {}
    for node in nodes:
        if node.kind is not NodeKind.TASK:
            continue
        issue_type = node.metadata.get("issue_type")
        key = node.metadata.get("key")
        if isinstance(issue_type, str) and issue_type:
            types[(key if isinstance(key, str) and key else node.id).upper()] = issue_type
    return types


def _latest_per_request(facts: Iterable[FactEvent], *, until: datetime) -> list[FactEvent]:
    """The newest fact of each request observed by ``until``.

    Of two facts for the same update, the one with the request's history wins:
    the re-read that added histories writes a second fact for an update.
    """
    latest: dict[tuple[str, str], FactEvent] = {}
    ordered = sorted(
        (fact for fact in facts if fact.observed_at <= until),
        key=lambda fact: (
            fact.observed_at,
            fact.payload.get("timeline_read") is True,
            fact.ingested_at,
        ),
    )
    for fact in ordered:
        repo = _text(fact.payload, "repo")
        number = _text(fact.payload, "id")
        if repo is not None and number is not None:
            latest[(repo, number)] = fact
    return list(latest.values())


def _author_id(fact: FactEvent) -> str | None:
    if fact.entity_ref.kind is NodeKind.DEVELOPER:
        return fact.entity_ref.id
    return None


def _in_scope(fact: FactEvent, scope: _Scope) -> bool:
    if scope.repos is not None and _text(fact.payload, "repo") not in scope.repos:
        return False
    return scope.members is None or _author_id(fact) in scope.members


def _item(
    fact: FactEvent,
    *,
    names: Mapping[str, str],
    issue_types: Mapping[str, str],
    window_end: datetime,
) -> PullRequestFlowItemView | None:
    payload = fact.payload
    is_merged = is_merged_merge_request(fact)
    if not is_merged and not is_open_merge_request(fact):
        return None
    member_id = _author_id(fact)
    login = _text(payload, "author")
    author_name = (names.get(member_id) if member_id else None) or _text(payload, "author_name")
    branch = _text(payload, "source_branch")
    title = _text(payload, "title") or ""
    classification = classify_request(
        title=title,
        branch=branch,
        labels=labels_from_payload(payload),
        author=login,
        author_name=author_name if member_id is None else None,
        issue_types=issue_types,
    )
    timeline = timeline_from_payload(payload)
    opened_at = payload_datetime(payload, "opened_at")
    merged_at = (payload_datetime(payload, "merged_at") or fact.observed_at) if is_merged else None
    draft = payload.get("draft") is True
    hours: Mapping[ReviewStage, float | None] = (
        stage_hours(timeline, merged_at=merged_at)
        if timeline is not None
        else dict.fromkeys(REVIEW_STAGE_ORDER)
    )
    stage, since = _open_stage(timeline, draft=draft, opened_at=opened_at, merged=is_merged)
    return PullRequestFlowItemView(
        repo=_text(payload, "repo") or "",
        number=_text(payload, "id") or "",
        title=title,
        web_url=_text(payload, "web_url"),
        author_name=author_name or login,
        request_type=classification.request_type,
        type_source=classification.source,
        type_evidence=classification.evidence,
        state="merged" if is_merged else "open",
        draft=draft,
        stage=stage,
        stage_since=since,
        stage_age_hours=_hours_between(since, window_end),
        stage_hours=hours,
        opened_at=opened_at,
        merged_at=merged_at,
        reviewer_count=timeline.reviewer_count if timeline is not None else 0,
        timed=timeline is not None,
    )


def _open_stage(
    timeline: ReviewTimeline | None,
    *,
    draft: bool,
    opened_at: datetime | None,
    merged: bool,
) -> tuple[ReviewStage | None, datetime | None]:
    if merged or timeline is None:
        return None, None
    return current_stage(timeline, draft=draft, opened_at=opened_at)


def _hours_between(start: datetime | None, end: datetime) -> float | None:
    if start is None:
        return None
    return max(0.0, (end - start).total_seconds() / 3600.0)


def _stage_view(
    stage: ReviewStage,
    merged: Sequence[PullRequestFlowItemView],
    open_now: Sequence[PullRequestFlowItemView],
) -> StageFlowView:
    values = [hours for item in merged if (hours := item.stage_hours[stage]) is not None]
    return StageFlowView(
        stage=stage,
        label=REVIEW_STAGE_LABELS[stage],
        p50_hours=percentile(values, 0.5),
        p75_hours=percentile(values, 0.75),
        measured_count=len(values),
        open_count=sum(1 for item in open_now if item.stage is stage),
    )


def _worst(stages: Sequence[StageFlowView], field: str) -> ReviewStage | None:
    """The stage requests spend longest in at that percentile: where work jams.

    None unless two stages or more are timed: with coding alone known (requests
    merged with no review), the longest stage is the only one, not a jam.
    """
    timed = [(getattr(view, field), view.stage) for view in stages if getattr(view, field)]
    return max(timed, key=lambda pair: pair[0])[1] if len(timed) > 1 else None


def _type_counts(
    merged: Sequence[PullRequestFlowItemView], open_now: Sequence[PullRequestFlowItemView]
) -> tuple[RequestTypeCountView, ...]:
    return tuple(
        RequestTypeCountView(
            request_type=request_type,
            label=REQUEST_TYPE_LABELS[request_type],
            merged_count=sum(1 for item in merged if item.request_type is request_type),
            open_count=sum(1 for item in open_now if item.request_type is request_type),
        )
        for request_type in REQUEST_TYPE_ORDER
    )


def _notes(scope: _Scope, *, untimed: int, unreviewed: int, no_commit: int) -> tuple[str, ...]:
    notes: list[str] = []
    if scope.empty_reason:
        notes.append(scope.empty_reason)
    if untimed:
        one = untimed == 1
        notes.append(
            f"{_count(untimed, 'request')} {'was' if one else 'were'} synced before review "
            f"histories were read, so {'its' if one else 'their'} stages are not known: "
            "counted, not timed. The next Git sync reads them."
        )
    if unreviewed:
        notes.append(
            f"{_count(unreviewed, 'merged request')} had no review by anyone but the author: "
            "counted for coding only, not as waiting for review."
        )
    if no_commit:
        one = no_commit == 1
        notes.append(
            f"{_count(no_commit, 'request')} {'has' if one else 'have'} no commit read, so "
            f"{'its' if one else 'their'} coding time is not known."
        )
    return tuple(notes)


def _count(count: int, noun: str) -> str:
    return f"1 {noun}" if count == 1 else f"{count} {noun}s"


def _text(payload: Mapping[str, JsonScalar], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and value:
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None
