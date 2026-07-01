from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from statistics import mean

from core.domain.errors import GraphNotFound
from core.domain.graph import EdgeKind, FactEvent, GraphNode, NodeKind
from core.ports.repositories import GraphRepository, TimeSeriesRepository

ACTIVE_WORK_ITEM_STATES = {"proposed", "in_progress", "in_review", "blocked"}
STALE_THRESHOLD_DAYS = 7
ABANDONED_THRESHOLD_DAYS = 21


@dataclass(frozen=True, kw_only=True)
class WorkItemFlowView:
    id: str
    name: str
    state: str
    item_type: str
    repo: str | None
    branch: str | None
    pr_id: str | None
    workstream_ids: tuple[str, ...]
    age_days: int | None
    cycle_time_days: float | None
    last_transition_at: datetime | None


@dataclass(frozen=True, kw_only=True)
class WorkstreamFlowSummaryView:
    workstream_id: str
    workstream_name: str
    active_count: int
    features_in_flight: int
    completed_count: int
    stale_count: int
    abandoned_count: int
    avg_cycle_time_days: float | None
    avg_pr_age_days: float | None


@dataclass(frozen=True, kw_only=True)
class WorkstreamFlowView:
    workstream_id: str
    workstream_name: str
    as_of: date
    active_count: int
    features_in_flight: int
    completed_count: int
    stale_count: int
    abandoned_count: int
    avg_cycle_time_days: float | None
    avg_pr_age_days: float | None
    work_items: tuple[WorkItemFlowView, ...]


@dataclass(frozen=True, kw_only=True)
class PortfolioFlowView:
    as_of: date
    active_count: int
    features_in_flight: int
    completed_count: int
    stale_count: int
    abandoned_count: int
    avg_cycle_time_days: float | None
    avg_pr_age_days: float | None
    workstreams: tuple[WorkstreamFlowSummaryView, ...]


@dataclass(frozen=True, kw_only=True)
class _WorkItemSnapshot:
    node: GraphNode
    state: str
    item_type: str
    repo: str | None
    branch: str | None
    pr_id: str | None
    created_at: datetime | None
    last_transition_at: datetime | None
    pr_opened_at: datetime | None
    done_at: datetime | None
    cycle_start_at: datetime | None
    workstream_ids: tuple[str, ...]


class FlowMetricsService:
    def __init__(
        self,
        graph_repository: GraphRepository,
        time_series_repository: TimeSeriesRepository,
    ) -> None:
        self._graph_repository = graph_repository
        self._time_series_repository = time_series_repository

    async def workstream_flow(
        self,
        tenant_id: str,
        workstream_id: str,
        as_of: date,
    ) -> WorkstreamFlowView:
        workstream = await self._ensure_workstream(tenant_id, workstream_id)
        snapshots = await self._work_item_snapshots(tenant_id, as_of)
        workstream_snapshots = [
            snapshot for snapshot in snapshots if workstream_id in snapshot.workstream_ids
        ]
        summary = _summarize_snapshots(workstream_snapshots, as_of=as_of)
        return WorkstreamFlowView(
            workstream_id=workstream.id,
            workstream_name=workstream.name,
            as_of=as_of,
            active_count=summary.active_count,
            features_in_flight=summary.features_in_flight,
            completed_count=summary.completed_count,
            stale_count=summary.stale_count,
            abandoned_count=summary.abandoned_count,
            avg_cycle_time_days=summary.avg_cycle_time_days,
            avg_pr_age_days=summary.avg_pr_age_days,
            work_items=tuple(
                _snapshot_to_view(snapshot, as_of) for snapshot in workstream_snapshots
            ),
        )

    async def portfolio_flow(self, tenant_id: str, as_of: date) -> PortfolioFlowView:
        workstreams = await self._graph_repository.list_nodes(tenant_id, NodeKind.WORKSTREAM)
        snapshots = await self._work_item_snapshots(tenant_id, as_of)
        summaries: list[WorkstreamFlowSummaryView] = []
        for workstream in workstreams:
            workstream_snapshots = [
                snapshot for snapshot in snapshots if workstream.id in snapshot.workstream_ids
            ]
            if not workstream_snapshots:
                continue
            summary = _summarize_snapshots(workstream_snapshots, as_of=as_of)
            summaries.append(
                WorkstreamFlowSummaryView(
                    workstream_id=workstream.id,
                    workstream_name=workstream.name,
                    active_count=summary.active_count,
                    features_in_flight=summary.features_in_flight,
                    completed_count=summary.completed_count,
                    stale_count=summary.stale_count,
                    abandoned_count=summary.abandoned_count,
                    avg_cycle_time_days=summary.avg_cycle_time_days,
                    avg_pr_age_days=summary.avg_pr_age_days,
                )
            )
        portfolio_summary = _summarize_snapshots(snapshots, as_of=as_of)
        return PortfolioFlowView(
            as_of=as_of,
            active_count=portfolio_summary.active_count,
            features_in_flight=portfolio_summary.features_in_flight,
            completed_count=portfolio_summary.completed_count,
            stale_count=portfolio_summary.stale_count,
            abandoned_count=portfolio_summary.abandoned_count,
            avg_cycle_time_days=portfolio_summary.avg_cycle_time_days,
            avg_pr_age_days=portfolio_summary.avg_pr_age_days,
            workstreams=tuple(
                sorted(summaries, key=lambda item: (item.workstream_name, item.workstream_id))
            ),
        )

    async def _ensure_workstream(self, tenant_id: str, workstream_id: str) -> GraphNode:
        node = await self._graph_repository.get_node(tenant_id, workstream_id)
        if node is None:
            raise GraphNotFound(f"workstream {workstream_id} not found for tenant {tenant_id}")
        if node.kind is not NodeKind.WORKSTREAM:
            raise GraphNotFound(f"{workstream_id} exists as a {node.kind.value}, not a workstream")
        return node

    async def _work_item_snapshots(  # noqa: C901
        self,
        tenant_id: str,
        as_of: date,
    ) -> list[_WorkItemSnapshot]:
        nodes = await self._graph_repository.list_nodes(tenant_id, NodeKind.WORK_ITEM)
        edges = await self._graph_repository.list_edges(tenant_id, kind=EdgeKind.CONTAINS)
        workstream_by_item: dict[str, list[str]] = {}
        for edge in edges:
            from_node = await self._graph_repository.get_node(tenant_id, edge.from_node_id)
            to_node = await self._graph_repository.get_node(tenant_id, edge.to_node_id)
            if (
                from_node is not None
                and to_node is not None
                and from_node.kind is NodeKind.WORKSTREAM
                and to_node.kind is NodeKind.WORK_ITEM
            ):
                workstream_by_item.setdefault(to_node.id, []).append(from_node.id)
        work_item_facts = await self._time_series_repository.list_recent_facts(
            tenant_id,
            sources=("work_item",),
            limit=max(1000, len(nodes) * 10),
        )
        pr_facts = await self._time_series_repository.list_recent_facts(
            tenant_id,
            sources=("vcs_pull_request",),
            limit=max(1000, len(nodes) * 10),
        )
        facts_by_item: dict[str, list[FactEvent]] = {}
        for fact in work_item_facts:
            facts_by_item.setdefault(fact.entity_ref.id, []).append(fact)
        pr_opened_by_repo_and_id: dict[tuple[str, str], datetime] = {}
        for fact in pr_facts:
            repo = _string_payload(fact.payload, "repo")
            pr_id = _string_payload(fact.payload, "id")
            if repo is None or pr_id is None:
                continue
            pr_opened_by_repo_and_id[(repo, pr_id)] = (
                _datetime_payload(fact.payload, "opened_at") or fact.observed_at
            )
        snapshots: list[_WorkItemSnapshot] = []
        for node in nodes:
            facts = sorted(
                facts_by_item.get(node.id, ()),
                key=lambda fact: (fact.observed_at, fact.ingested_at, fact.correlation_id),
            )
            state = _string_metadata(node, "state") or "proposed"
            item_type = _string_metadata(node, "item_type") or "feature"
            repo = _string_metadata(node, "repo")
            branch = _string_metadata(node, "branch")
            pr_id = _string_metadata(node, "pr_id")
            created_at = _datetime_metadata(node, "created_at")
            last_transition_at = _datetime_metadata(node, "last_transition_at")
            pr_opened_at = (
                pr_opened_by_repo_and_id.get((repo, pr_id))
                if repo is not None and pr_id is not None
                else None
            )
            cycle_start_at = created_at
            done_at = None
            for fact in facts:
                to_state = _string_payload(fact.payload, "to_state")
                if cycle_start_at is None and to_state in {"in_progress", "in_review", "done"}:
                    cycle_start_at = fact.observed_at
                if to_state == "done":
                    done_at = fact.observed_at
                    if cycle_start_at is None:
                        cycle_start_at = fact.observed_at
                if to_state is not None:
                    last_transition_at = fact.observed_at
            workstream_ids = tuple(sorted(dict.fromkeys(workstream_by_item.get(node.id, ()))))
            snapshots.append(
                _WorkItemSnapshot(
                    node=node,
                    state=state,
                    item_type=item_type,
                    repo=repo,
                    branch=branch,
                    pr_id=pr_id,
                    created_at=created_at,
                    last_transition_at=last_transition_at,
                    pr_opened_at=pr_opened_at,
                    done_at=done_at,
                    cycle_start_at=cycle_start_at,
                    workstream_ids=workstream_ids,
                )
            )
        return sorted(snapshots, key=lambda snapshot: (snapshot.node.name, snapshot.node.id))


@dataclass(frozen=True, kw_only=True)
class _SummaryStats:
    active_count: int
    features_in_flight: int
    completed_count: int
    stale_count: int
    abandoned_count: int
    avg_cycle_time_days: float | None
    avg_pr_age_days: float | None


def _summarize_snapshots(
    snapshots: Sequence[_WorkItemSnapshot],
    *,
    as_of: date,
) -> _SummaryStats:
    active_count = sum(1 for snapshot in snapshots if snapshot.state in ACTIVE_WORK_ITEM_STATES)
    features_in_flight = sum(
        1
        for snapshot in snapshots
        if snapshot.state in ACTIVE_WORK_ITEM_STATES and snapshot.item_type == "feature"
    )
    completed_count = sum(1 for snapshot in snapshots if snapshot.state == "done")
    abandoned_count = 0
    stale_count = 0
    cycle_times: list[float] = []
    pr_ages: list[float] = []
    for snapshot in snapshots:
        age_days = _age_days(snapshot, as_of)
        if snapshot.state == "abandoned":
            abandoned_count += 1
        elif snapshot.state in ACTIVE_WORK_ITEM_STATES and age_days is not None:
            if age_days >= ABANDONED_THRESHOLD_DAYS:
                abandoned_count += 1
            elif age_days >= STALE_THRESHOLD_DAYS:
                stale_count += 1
        if snapshot.done_at is not None and snapshot.cycle_start_at is not None:
            cycle_time = _duration_days(snapshot.cycle_start_at, snapshot.done_at)
            if cycle_time is not None:
                cycle_times.append(cycle_time)
        if snapshot.pr_id is not None:
            pr_age = _age_days_from_datetime(snapshot.pr_opened_at or snapshot.created_at, as_of)
            if pr_age is not None:
                pr_ages.append(float(pr_age))
    return _SummaryStats(
        active_count=active_count,
        features_in_flight=features_in_flight,
        completed_count=completed_count,
        stale_count=stale_count,
        abandoned_count=abandoned_count,
        avg_cycle_time_days=mean(cycle_times) if cycle_times else None,
        avg_pr_age_days=mean(pr_ages) if pr_ages else None,
    )


def _snapshot_to_view(snapshot: _WorkItemSnapshot, as_of: date) -> WorkItemFlowView:
    return WorkItemFlowView(
        id=snapshot.node.id,
        name=snapshot.node.name,
        state=snapshot.state,
        item_type=snapshot.item_type,
        repo=snapshot.repo,
        branch=snapshot.branch,
        pr_id=snapshot.pr_id,
        workstream_ids=snapshot.workstream_ids,
        age_days=_age_days(snapshot, as_of),
        cycle_time_days=(
            _duration_days(snapshot.cycle_start_at, snapshot.done_at) if snapshot.done_at else None
        ),
        last_transition_at=snapshot.last_transition_at,
    )


def _age_days(snapshot: _WorkItemSnapshot, as_of: date | None = None) -> int | None:
    reference_at = snapshot.last_transition_at or snapshot.created_at
    if reference_at is None:
        return None
    reference_date = as_of or date.today()
    return max(0, (reference_date - reference_at.date()).days)


def _age_days_from_datetime(reference_at: datetime | None, as_of: date) -> int | None:
    if reference_at is None:
        return None
    return max(0, (as_of - reference_at.date()).days)


def _duration_days(start_at: datetime | None, end_at: datetime | None) -> float | None:
    if start_at is None or end_at is None:
        return None
    seconds = (end_at - start_at).total_seconds()
    if seconds < 0:
        return None
    return seconds / 86400.0


def _string_metadata(node: GraphNode, key: str) -> str | None:
    value = node.metadata.get(key)
    return value if isinstance(value, str) and value else None


def _datetime_metadata(node: GraphNode, key: str) -> datetime | None:
    value = node.metadata.get(key)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _datetime_payload(payload: Mapping[str, object], key: str) -> datetime | None:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _string_payload(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None
