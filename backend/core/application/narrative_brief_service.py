from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from core.application.brief_facts import (
    BriefFacts,
    BriefInputs,
    BriefScope,
    StatusFacts,
    build_brief_facts,
    heatmap_status,
    no_status,
    pod_status,
    project_status,
)
from core.application.brief_grounding import ground_brief
from core.application.persona_views import PersonaViewService
from core.application.portfolio_feed_service import PortfolioFeedService
from core.domain.brief import BriefKind, NarrativeBrief
from core.domain.errors import GraphNotFound
from core.domain.graph import EdgeKind, GraphNode, GraphTree, NodeKind
from core.domain.llm import LlmMessage, LlmRequest
from core.ports.llm import LlmProvider
from core.ports.repositories import GraphRepository, NarrativeBriefRepository, StatusRepository

BRIEF_SYSTEM_PROMPT = (
    "You write concise, descriptive program-management briefs for leaders. "
    "Summarize ONLY the delivery facts, feed items, and status rollups provided. "
    "Do not invent details, do not add recommendations, and never include raw "
    "chat, DM, or reply content. Write two to four short sentences of plain prose. "
    "Name only the issues and people the facts name. Call an issue done or "
    "completed only when the facts say the tracker shows it done: a merged merge "
    "request on an open ticket is 'merged, ticket still open'. State blockers as "
    "the facts give them, cleared ones included. Never say that anything was "
    "rescheduled, postponed or cancelled unless a fact says so."
)

# Per-kind lookback windows for the descriptive feed context.
_LOOKBACK_DAYS: dict[BriefKind, int] = {
    BriefKind.DAILY_POD: 1,
    BriefKind.WEEKLY_PROJECT: 7,
    BriefKind.EXEC: 7,
}

# Every feed item of the window is read, up to this many, and aggregated:
# the newest twelve hid a blocker the thirteenth reported (N38).
_MAX_FEED_ITEMS = 500

_LABELS: dict[BriefKind, str] = {
    BriefKind.DAILY_POD: "Daily pod summary",
    BriefKind.WEEKLY_PROJECT: "Weekly project update",
    BriefKind.EXEC: "Executive brief",
}


@dataclass(frozen=True, kw_only=True)
class _ScopedStatus:
    scope: BriefScope
    status: StatusFacts


class NarrativeBriefService:
    def __init__(
        self,
        llm_provider: LlmProvider,
        feed_service: PortfolioFeedService,
        persona_view_service: PersonaViewService,
        graph_repository: GraphRepository,
        brief_repository: NarrativeBriefRepository,
        model: str,
        status_repository: StatusRepository | None = None,
    ) -> None:
        """``status_repository`` says which blockers are open now; without it a
        brief states the blockers check-ins reported, never whether they cleared."""
        self._llm_provider = llm_provider
        self._feed_service = feed_service
        self._persona_view_service = persona_view_service
        self._graph_repository = graph_repository
        self._brief_repository = brief_repository
        self._model = model
        self._status_repository = status_repository

    async def generate(
        self,
        tenant_id: str,
        kind: BriefKind,
        scope_id: str,
        *,
        as_of: datetime,
    ) -> NarrativeBrief:
        facts = await self.facts(tenant_id, kind, scope_id, as_of=as_of)
        scope_name = await self._scope_name(tenant_id, kind, scope_id)
        title = _title_for(kind, scope_name)
        body = await self._compose_body(tenant_id, kind, scope_id, as_of, title, facts)
        brief = NarrativeBrief(
            tenant_id=tenant_id,
            kind=kind,
            scope_id=scope_id,
            title=title,
            body=body,
            generated_at=as_of,
            sources=facts.sources,
        )
        await self._brief_repository.record_brief(brief)
        return brief

    async def facts(
        self,
        tenant_id: str,
        kind: BriefKind,
        scope_id: str,
        *,
        as_of: datetime,
    ) -> BriefFacts:
        """What a brief of this scope may say: its facts of the window, aggregated.

        A pod's or a project's brief reads its own members' check-ins and its
        own issues only (N39); the exec brief reads the whole tenant.
        """
        lookback = _LOOKBACK_DAYS.get(kind, 7)
        since = as_of - timedelta(days=lookback)
        scoped = await self._scoped_status(tenant_id, kind, scope_id, as_of.date())
        feed = await self._feed_service.feed(tenant_id, since=since, limit=_MAX_FEED_ITEMS)
        items = tuple(item for item in feed.items if item.observed_at <= as_of)
        if len(feed.items) >= _MAX_FEED_ITEMS and feed.items:
            # More happened than was read: say the window the facts cover.
            since = max(since, min(item.observed_at for item in feed.items))
        tasks = {
            node.id: node
            for node in await self._graph_repository.list_nodes(tenant_id, NodeKind.TASK)
        }
        people = {
            node.id: node.name
            for node in await self._graph_repository.list_nodes(tenant_id, NodeKind.DEVELOPER)
        }
        repos = frozenset(
            node.name or node.id
            for node in await self._graph_repository.list_nodes(tenant_id, NodeKind.REPO)
        )
        scope_name = await self._scope_name(tenant_id, kind, scope_id)
        person_ids = (
            scoped.scope.member_ids if scoped.scope.member_ids is not None else frozenset(people)
        )
        return build_brief_facts(
            BriefInputs(
                label=_LABELS[kind],
                scope_name=scope_name,
                as_of=as_of,
                since=since,
                status=scoped.status,
                scope=scoped.scope,
                items=items,
                tasks=tasks,
                people=people,
                repos=repos,
                open_blockers=await self._open_blockers(tenant_id, person_ids, as_of.date()),
                scope_ref=_scope_ref(kind, scope_id),
            )
        )

    async def _compose_body(
        self,
        tenant_id: str,
        kind: BriefKind,
        scope_id: str,
        as_of: datetime,
        title: str,
        facts: BriefFacts,
    ) -> str:
        context = facts.context
        fallback = _fallback_body(context)
        request = LlmRequest(
            tenant_id=tenant_id,
            prompt=f"{title}\n\n{context}",
            model=self._model,
            correlation_id=f"brief-{kind.value}-{scope_id or 'portfolio'}-{as_of.isoformat()}",
            system=BRIEF_SYSTEM_PROMPT,
            messages=(LlmMessage(role="user", content=context),),
            metadata={
                "agent": "narrative_brief_service",
                "purpose": "narrative_brief",
                "kind": kind.value,
            },
        )
        try:
            response = await self._llm_provider.complete(request)
        except Exception:
            return fallback
        # Checked against the facts, sentence by sentence: what no fact
        # supports is corrected or dropped, never trusted to the prompt alone.
        grounded = ground_brief(response.text, facts)
        return grounded.body or fallback

    async def _scoped_status(
        self,
        tenant_id: str,
        kind: BriefKind,
        scope_id: str,
        as_of: date,
    ) -> _ScopedStatus:
        try:
            if kind is BriefKind.DAILY_POD:
                return await self._pod_scope(tenant_id, scope_id, as_of)
            if kind is BriefKind.WEEKLY_PROJECT:
                return await self._project_scope(tenant_id, scope_id, as_of)
            heatmap = await self._persona_view_service.portfolio_heatmap(tenant_id, as_of)
            return _ScopedStatus(scope=BriefScope(), status=heatmap_status(heatmap.cells))
        except GraphNotFound:
            return _ScopedStatus(scope=BriefScope.empty(), status=no_status())

    async def _pod_scope(self, tenant_id: str, pod_id: str, as_of: date) -> _ScopedStatus:
        """A pod's members, and its own issues (N39).

        Its issues are the ones it holds (``pod --contains--> task``) and the
        ones its task panel lists that no other pod holds. The panel lists a
        member's task wherever a project above the pod owns it, so a member in
        two pods would otherwise bring the other pod's tickets along.
        """
        view = await self._persona_view_service.pod_checkins(tenant_id, pod_id, as_of)
        panel = await self._persona_view_service.pod_tasks(tenant_id, pod_id, as_of)
        tree = await self._graph_repository.get_program_tree(tenant_id, pod_id, as_of)
        held = _contained(tree)
        holders = await self._pod_holders(tenant_id, as_of)
        issue_ids = {node.id for node in held if node.kind is NodeKind.TASK}
        issue_ids.update(
            task.id
            for task in panel.tasks
            if not holders.get(task.id) or pod_id in holders[task.id]
        )
        members = {
            developer.developer_id: developer.developer_name for developer in view.developers
        }
        return _ScopedStatus(
            scope=BriefScope(
                member_ids=frozenset(members),
                member_names=tuple(sorted(members.values())),
                issue_ids=frozenset(issue_ids),
                node_ids=frozenset(node.id for node in held),
                repos=_repos(tree.root, held),
            ),
            status=pod_status(view),
        )

    async def _pod_holders(self, tenant_id: str, as_of: date) -> dict[str, set[str]]:
        """The pods that hold each node outright on ``as_of``."""
        pods = {
            node.id for node in await self._graph_repository.list_nodes(tenant_id, NodeKind.POD)
        }
        holders: dict[str, set[str]] = {}
        for edge in await self._graph_repository.list_edges(tenant_id, kind=EdgeKind.CONTAINS):
            if edge.from_node_id in pods and edge.is_active_on(as_of):
                holders.setdefault(edge.to_node_id, set()).add(edge.from_node_id)
        return holders

    async def _project_scope(self, tenant_id: str, project_id: str, as_of: date) -> _ScopedStatus:
        """A project's people, and the issues it owns (not those of a pod it shares)."""
        progress = await self._persona_view_service.project_progress(tenant_id, project_id, as_of)
        tree = await self._graph_repository.get_program_tree(tenant_id, project_id, as_of)
        held = _contained(tree)
        members = {node.id: node.name for node in held if node.kind is NodeKind.DEVELOPER}
        return _ScopedStatus(
            scope=BriefScope(
                member_ids=frozenset(members),
                member_names=tuple(sorted(members.values())),
                issue_ids=frozenset(task.id for task in progress.tasks),
                node_ids=frozenset(node.id for node in held),
                repos=_repos(tree.root, held),
            ),
            status=project_status(progress),
        )

    async def _open_blockers(
        self, tenant_id: str, person_ids: Iterable[str], as_of: date
    ) -> dict[str, int] | None:
        if self._status_repository is None:
            return None
        wanted = sorted(set(person_ids))
        counts = dict.fromkeys(wanted, 0)
        if not wanted:
            return counts
        for blocker in await self._status_repository.open_blockers_for_developers(
            tenant_id, wanted, as_of
        ):
            counts[blocker.developer_id] = counts.get(blocker.developer_id, 0) + 1
        return counts

    async def _scope_name(
        self,
        tenant_id: str,
        kind: BriefKind,
        scope_id: str,
    ) -> str:
        if kind is BriefKind.EXEC or not scope_id:
            return "portfolio"
        node = await self._graph_repository.get_node(tenant_id, scope_id)
        return node.name if node is not None else scope_id


def _contained(tree: GraphTree) -> tuple[GraphNode, ...]:
    """Every node the root holds along ``contains`` on the tree's day.

    A tree also follows ``assigned_to``: a member's tasks in another pod and a
    served workstream's whole backlog would come along. Walking ``contains``
    alone keeps what the root holds. Below a project it takes only the people
    and repositories of its pods, since a pod two projects share holds both
    projects' tickets, and it never walks into a project or program.
    """
    nodes = {node.id: node for node in tree.nodes}
    children: dict[str, list[str]] = {}
    for edge in tree.edges:
        if edge.kind is EdgeKind.CONTAINS:
            children.setdefault(edge.from_node_id, []).append(edge.to_node_id)
    held: dict[str, GraphNode] = {}
    queue = list(children.get(tree.root.id, ()))
    while queue:
        node_id = queue.pop()
        node = nodes.get(node_id)
        if node is None or node_id in held:
            continue
        held[node_id] = node
        if node.kind is NodeKind.POD and tree.root.kind is NodeKind.PROJECT:
            # The project's pods hold its people; their tasks are counted by
            # ownership (project_progress), not by the pod they sit in.
            queue.extend(
                child
                for child in children.get(node_id, ())
                if (target := nodes.get(child)) is not None
                and target.kind in {NodeKind.DEVELOPER, NodeKind.REPO}
            )
            continue
        if node.kind not in {NodeKind.PROJECT, NodeKind.PROGRAM}:
            queue.extend(children.get(node_id, ()))
    return tuple(held.values())


def _repos(root: GraphNode, held: Iterable[GraphNode]) -> frozenset[str]:
    repos = {node.name or node.id for node in held if node.kind is NodeKind.REPO}
    configured = root.metadata.get("github_repos")
    if isinstance(configured, str):
        repos.update(repo.strip() for repo in configured.split(",") if repo.strip())
    return frozenset(repos)


def _scope_ref(kind: BriefKind, scope_id: str) -> str | None:
    if not scope_id:
        return None
    return f"{'pod' if kind is BriefKind.DAILY_POD else 'project'}:{scope_id}"


def _title_for(kind: BriefKind, scope_name: str) -> str:
    if kind is BriefKind.DAILY_POD:
        return f"Daily pod summary: {scope_name}"
    if kind is BriefKind.WEEKLY_PROJECT:
        return f"Weekly project update: {scope_name}"
    return "Executive portfolio brief"


def _fallback_body(context: str) -> str:
    # Deterministic, privacy-safe summary used when the model output is empty or
    # unusable. Collapses the descriptive context into a single line.
    collapsed = " ".join(line.strip("- ").strip() for line in context.splitlines() if line.strip())
    return collapsed or "No activity to report for this period."
