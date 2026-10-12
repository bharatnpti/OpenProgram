"""Profiles per pod membership: read, set, suggested, and handed to the readers that route by them.

Who sets a profile (D8 keeps them rights-free, so this is about routing only):

- an admin, in every pod;
- a scrum master, in the pods they run (``ForecastService.runs_pod``: they are
  its scrum master contact or one of its members) -- nowhere else.

A membership with no row reads its profile from its pod role and says so
(``source`` None): it is not checked. Rows written by the migration's backfill
are not checked either. A profile someone chose (an admin, an import, an
accepted suggestion) is checked, and only a checked profile changes the
code-work test (``code_work_by_pod``); the others keep today's app-role rule,
since a pod role was never meant to say what discipline a person brings.

A row counts only from the start of the membership it belongs to: someone
taken out of a pod and added back later starts again from the pod role, not
from the profile they once had.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta

from core.application.authorization import AuthorizationPolicy, Capability
from core.domain.auth import Principal, Role
from core.domain.errors import AuthorizationDenied, GraphNotFound
from core.domain.graph import EdgeKind, FactEvent, GraphEdge, GraphNode, NodeKind
from core.domain.profiles import (
    SUGGESTION_WINDOW_DAYS,
    PodMemberProfile,
    ProfileConflict,
    ProfileDefinition,
    ProfileError,
    ProfileSource,
    ProfileSuggestion,
    SuggestionInputs,
    catalog_by_slug,
    ensure_offered,
    merged_catalog,
    profile_for_pod_role,
    profile_label,
    same_name_as,
    suggest_profile,
    validated_entry,
)
from core.ports.directory import DirectoryUserRepository
from core.ports.profiles import ProfileRepository
from core.ports.repositories import GraphRepository, TimeSeriesRepository

#: Whether ``subject`` runs the pod on the day (ForecastService.runs_pod).
RunsPod = Callable[[str, str, str, date], Awaitable[bool]]

NOT_A_SETTER = "Only an admin, or a scrum master in a pod they run, sets profiles."
NOT_YOUR_POD = "You set profiles in the pods you run, and this is not one of them."
_MEMBERSHIP_START = date(1970, 1, 1)
_GIT_SOURCES = ("vcs_commit", "vcs_pull_request")
_GIT_SCAN_LIMIT = 2000


@dataclass(frozen=True, kw_only=True)
class MemberProfileView:
    """One member's profile in one pod, on the day read."""

    pod_id: str
    pod_name: str
    member_id: str
    member_name: str
    #: The pod role on the membership (developer, scrum_master, ...).
    role: str | None
    profile: str
    label: str
    code_work: bool
    #: None when no row holds it: the profile is the pod role's default.
    source: ProfileSource | None
    #: Someone chose it (an admin, an import, an accepted suggestion).
    checked: bool
    #: The day the profile holds from; None when it is the pod role's default.
    since: date | None = None
    set_by: str | None = None
    set_by_name: str | None = None
    set_at: datetime | None = None
    is_lead: bool = False


@dataclass(frozen=True, kw_only=True)
class PodProfilesView:
    pod_id: str
    pod_name: str
    as_of: date
    members: tuple[MemberProfileView, ...]


@dataclass(frozen=True, kw_only=True)
class MembershipProfileDetail:
    """What whoever sets a profile sees: the current one, its history and a suggestion."""

    pod_id: str
    pod_name: str
    member_id: str
    member_name: str
    #: None when the person is not in the pod (yet): a suggestion before adding them.
    current: MemberProfileView | None
    history: tuple[PodMemberProfile, ...]
    suggestion: ProfileSuggestion
    suggestion_label: str


@dataclass(frozen=True, kw_only=True)
class ReviewItem:
    current: MemberProfileView
    suggestion: ProfileSuggestion
    suggestion_label: str


@dataclass(frozen=True, kw_only=True)
class AcceptChoice:
    pod_id: str
    member_id: str
    profile: str


@dataclass(frozen=True, kw_only=True)
class _Membership:
    pod: GraphNode
    member: GraphNode
    edge: GraphEdge

    @property
    def start(self) -> date:
        return self.edge.valid_from or _MEMBERSHIP_START

    @property
    def role(self) -> str | None:
        value = self.edge.metadata.get("role")
        return value.strip() if isinstance(value, str) and value.strip() else None


class ProfileService:
    def __init__(
        self,
        *,
        repository: ProfileRepository,
        graph_repository: GraphRepository,
        directory_repository: DirectoryUserRepository | None = None,
        time_series_repository: TimeSeriesRepository | None = None,
        runs_pod: RunsPod | None = None,
        policy: AuthorizationPolicy | None = None,
        today: Callable[[], date] = date.today,
        clock: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
    ) -> None:
        self._repository = repository
        self._graph = graph_repository
        self._directory = directory_repository
        self._facts = time_series_repository
        self._runs_pod = runs_pod
        self._policy = policy or AuthorizationPolicy()
        self._today = today
        self._clock = clock

    # ---- the catalog ------------------------------------------------------------------

    async def catalog(self, tenant_id: str) -> tuple[ProfileDefinition, ...]:
        """The shipped defaults with the tenant's changes over them."""
        return merged_catalog(await self._repository.list_catalog(tenant_id))

    async def save_catalog_entry(
        self, tenant_id: str, entry: ProfileDefinition, *, by: str
    ) -> ProfileDefinition:
        """Rename, hide or add an entry. A hidden one stays on the memberships holding it."""
        tidy = validated_entry(entry)
        current = await self.catalog(tenant_id)
        known = catalog_by_slug(current)
        if tidy.slug not in known and len(current) >= 30:
            raise ProfileError("A tenant keeps at most 30 profiles. Hide one you don't use.")
        if tidy.hidden and all(item.hidden or item.slug == tidy.slug for item in current):
            raise ProfileError("Keep at least one profile offered.")
        taken = same_name_as(tidy.label, tidy.slug, current)
        if taken is not None:
            raise ProfileConflict(
                f"Another profile ({taken.slug}) is already called {taken.label!r}. "
                "Give this one a different name."
            )
        await self._repository.save_catalog_entry(tenant_id, tidy, updated_by=by)
        return next(item for item in await self.catalog(tenant_id) if item.slug == tidy.slug)

    async def ensure_offered(self, tenant_id: str, slug: str) -> ProfileDefinition:
        return ensure_offered(slug, await self.catalog(tenant_id))

    # ---- who may set them -------------------------------------------------------------

    async def can_set(self, principal: Principal, pod_id: str) -> bool:
        if principal.has_role(Role.ADMIN):
            return True
        if not self._policy.can(principal, Capability.SET_POD_PROFILES):
            return False
        if self._runs_pod is None:
            return False
        return await self._runs_pod(principal.tenant_id, pod_id, principal.subject, self._today())

    async def ensure_can_set(self, principal: Principal, pod_id: str) -> None:
        if await self.can_set(principal, pod_id):
            return
        if self._policy.can(principal, Capability.SET_POD_PROFILES):
            raise AuthorizationDenied(NOT_YOUR_POD)
        raise AuthorizationDenied(NOT_A_SETTER)

    async def settable_pods(self, principal: Principal) -> tuple[str, ...] | None:
        """The pods whose profiles the person sets: None for every pod (an admin)."""
        if principal.has_role(Role.ADMIN):
            return None
        pods = await self._graph.list_nodes(principal.tenant_id, NodeKind.POD)
        return tuple([pod.id for pod in pods if await self.can_set(principal, pod.id)])

    # ---- reading ----------------------------------------------------------------------

    async def pod_profiles(self, tenant_id: str, pod_id: str, as_of: date) -> PodProfilesView:
        pod = await self._graph.get_node(tenant_id, pod_id, as_of=as_of)
        if pod is None or pod.kind is not NodeKind.POD:
            raise GraphNotFound(f"No pod {pod_id!r}.")
        views = await self._views(tenant_id, as_of, pod_ids=[pod_id])
        members = sorted(
            (view for view in views.values() if view.pod_id == pod_id),
            key=lambda view: (view.member_name.casefold(), view.member_id),
        )
        return PodProfilesView(
            pod_id=pod.id, pod_name=pod.name, as_of=as_of, members=tuple(members)
        )

    async def member_profiles(
        self, tenant_id: str, member_id: str, as_of: date
    ) -> tuple[MemberProfileView, ...]:
        """The person's own profiles, one per pod they are in."""
        views = await self._views(tenant_id, as_of, member_id=member_id)
        return tuple(sorted(views.values(), key=lambda view: view.pod_name.casefold()))

    async def profiles_on(
        self, tenant_id: str, as_of: date, *, pod_ids: Sequence[str] | None = None
    ) -> dict[tuple[str, str], MemberProfileView]:
        """Every membership's profile on the day, by (pod, member), for the readers that route."""
        return await self._views(tenant_id, as_of, pod_ids=pod_ids)

    async def code_work_by_pod(
        self, tenant_id: str, member_id: str, as_of: date
    ) -> dict[str, bool]:
        """Whether a checked profile of the person expects code work, per pod.

        Pods where their profile is unchecked (backfilled, or the pod role's
        default) are left out, so the caller keeps its own rule there.
        """
        views = await self._views(tenant_id, as_of, member_id=member_id)
        return {view.pod_id: view.code_work for view in views.values() if view.checked}

    async def membership_detail(
        self, tenant_id: str, pod_id: str, member_id: str, *, role: str | None = None
    ) -> MembershipProfileDetail:
        today = self._today()
        pod = await self._node(tenant_id, pod_id, NodeKind.POD)
        member = await self._node(tenant_id, member_id, NodeKind.DEVELOPER)
        views = await self._views(tenant_id, today, pod_ids=[pod_id])
        current = views.get((pod_id, member_id))
        rows = await self._repository.list_member_profiles(
            tenant_id, pod_ids=[pod_id], member_id=member_id
        )
        catalog = await self.catalog(tenant_id)
        suggestion = await self._suggest(
            tenant_id,
            pod,
            member,
            role if role is not None else (current.role if current else None),
            catalog,
            await self._git_counts(tenant_id),
        )
        return MembershipProfileDetail(
            pod_id=pod.id,
            pod_name=pod.name,
            member_id=member.id,
            member_name=member.name,
            current=current,
            history=tuple(sorted(rows, key=lambda row: row.valid_from, reverse=True)),
            suggestion=suggestion,
            suggestion_label=profile_label(suggestion.profile, catalog),
        )

    async def review(
        self, tenant_id: str, pod_ids: Collection[str] | None = None
    ) -> tuple[ReviewItem, ...]:
        """Memberships whose profile nobody checked yet, each with a suggestion beside it."""
        today = self._today()
        views = await self._views(
            tenant_id, today, pod_ids=list(pod_ids) if pod_ids is not None else None
        )
        unchecked = [view for view in views.values() if not view.checked]
        if not unchecked:
            return ()
        catalog = await self.catalog(tenant_id)
        git = await self._git_counts(tenant_id)
        items: list[ReviewItem] = []
        for view in sorted(
            unchecked, key=lambda item: (item.pod_name.casefold(), item.member_name.casefold())
        ):
            pod = await self._graph.get_node(tenant_id, view.pod_id)
            member = await self._graph.get_node(tenant_id, view.member_id)
            if pod is None or member is None:
                continue
            suggestion = await self._suggest(tenant_id, pod, member, view.role, catalog, git)
            items.append(
                ReviewItem(
                    current=view,
                    suggestion=suggestion,
                    suggestion_label=profile_label(suggestion.profile, catalog),
                )
            )
        return tuple(items)

    # ---- writing ----------------------------------------------------------------------

    async def set_profile(
        self,
        tenant_id: str,
        pod_id: str,
        member_id: str,
        profile: str,
        *,
        source: ProfileSource,
        by: str,
    ) -> MemberProfileView:
        """The member's profile in the pod from today; earlier days keep theirs."""
        if source is ProfileSource.BACKFILL:
            raise ProfileError("Only the migration writes a profile from the pod role.")
        today = self._today()
        await self._node(tenant_id, pod_id, NodeKind.POD)
        await self._node(tenant_id, member_id, NodeKind.DEVELOPER)
        entry = await self.ensure_offered(tenant_id, profile)
        if await self._membership(tenant_id, pod_id, member_id, today) is None:
            raise GraphNotFound(f"{member_id} is not in {pod_id}; add them to the pod first.")
        await self._repository.save_member_profile(
            PodMemberProfile(
                tenant_id=tenant_id,
                pod_id=pod_id,
                member_id=member_id,
                profile=entry.slug,
                valid_from=today,
                source=source,
                set_by=by,
                set_at=self._clock(),
            )
        )
        views = await self._views(tenant_id, today, pod_ids=[pod_id])
        return views[(pod_id, member_id)]

    async def accept(
        self, principal: Principal, choices: Sequence[AcceptChoice]
    ) -> tuple[MemberProfileView, ...]:
        """Accept the suggestions shown, one row each, refusing any pod the person does not set."""
        for choice in choices:
            await self.ensure_can_set(principal, choice.pod_id)
        catalog = await self.catalog(principal.tenant_id)
        for choice in choices:
            ensure_offered(choice.profile, catalog)
        accepted: list[MemberProfileView] = []
        for choice in choices:
            accepted.append(
                await self.set_profile(
                    principal.tenant_id,
                    choice.pod_id,
                    choice.member_id,
                    choice.profile,
                    source=ProfileSource.SUGGESTION_ACCEPTED,
                    by=principal.subject,
                )
            )
        return tuple(accepted)

    # ---- internals --------------------------------------------------------------------

    async def _node(self, tenant_id: str, node_id: str, kind: NodeKind) -> GraphNode:
        node = await self._graph.get_node(tenant_id, node_id)
        if node is None or node.kind is not kind:
            raise GraphNotFound(f"No {kind.value} {node_id!r}.")
        return node

    async def _memberships(
        self,
        tenant_id: str,
        as_of: date,
        *,
        pod_ids: Sequence[str] | None = None,
        member_id: str | None = None,
    ) -> list[_Membership]:
        nodes = {node.id: node for node in await self._graph.list_nodes(tenant_id, as_of=as_of)}
        wanted = set(pod_ids) if pod_ids is not None else None
        found: dict[tuple[str, str], _Membership] = {}
        for edge in await self._graph.list_edges(
            tenant_id, to_node_id=member_id, kind=EdgeKind.CONTAINS
        ):
            if not edge.is_active_on(as_of) or (
                wanted is not None and edge.from_node_id not in wanted
            ):
                continue
            pod, member = nodes.get(edge.from_node_id), nodes.get(edge.to_node_id)
            if pod is None or member is None:
                continue
            if pod.kind is not NodeKind.POD or member.kind is not NodeKind.DEVELOPER:
                continue
            found.setdefault((pod.id, member.id), _Membership(pod=pod, member=member, edge=edge))
        return list(found.values())

    async def _membership(
        self, tenant_id: str, pod_id: str, member_id: str, as_of: date
    ) -> _Membership | None:
        for membership in await self._memberships(
            tenant_id, as_of, pod_ids=[pod_id], member_id=member_id
        ):
            return membership
        return None

    async def _views(
        self,
        tenant_id: str,
        as_of: date,
        *,
        pod_ids: Sequence[str] | None = None,
        member_id: str | None = None,
    ) -> dict[tuple[str, str], MemberProfileView]:
        memberships = await self._memberships(
            tenant_id, as_of, pod_ids=pod_ids, member_id=member_id
        )
        if not memberships:
            return {}
        rows = await self._repository.list_member_profiles(
            tenant_id,
            pod_ids=sorted({item.pod.id for item in memberships}),
            member_id=member_id,
        )
        by_key: dict[tuple[str, str], list[PodMemberProfile]] = {}
        for row in rows:
            by_key.setdefault((row.pod_id, row.member_id), []).append(row)
        catalog = catalog_by_slug(await self.catalog(tenant_id))
        names = {
            node.id: node.name
            for node in await self._graph.list_nodes(tenant_id, NodeKind.DEVELOPER)
        }
        views: dict[tuple[str, str], MemberProfileView] = {}
        for item in memberships:
            key = (item.pod.id, item.member.id)
            # Only rows of this membership: someone added back starts from the pod role again.
            held = [row for row in by_key.get(key, ()) if item.start <= row.valid_from <= as_of]
            current = max(held, key=_row_order) if held else None
            slug = current.profile if current is not None else profile_for_pod_role(item.role)
            entry = catalog.get(slug)
            view = MemberProfileView(
                pod_id=item.pod.id,
                pod_name=item.pod.name,
                member_id=item.member.id,
                member_name=item.member.name,
                role=item.role,
                profile=slug,
                label=entry.label if entry else profile_label(slug, catalog.values()),
                code_work=entry.code_work if entry else False,
                source=None,
                checked=False,
            )
            if current is not None:
                view = replace(
                    view,
                    source=current.source,
                    checked=current.checked,
                    since=current.valid_from,
                    set_by=current.set_by,
                    set_by_name=names.get(current.set_by or ""),
                    set_at=current.set_at,
                    is_lead=current.is_lead,
                )
            views[key] = view
        return views

    async def _suggest(
        self,
        tenant_id: str,
        pod: GraphNode,
        member: GraphNode,
        role: str | None,
        catalog: Sequence[ProfileDefinition],
        git: Mapping[str, int],
    ) -> ProfileSuggestion:
        types, labels = await self._issue_kinds(tenant_id, pod.id, member.id)
        return suggest_profile(
            catalog,
            SuggestionInputs(
                title=await self._title(tenant_id, member),
                issue_types=types,
                issue_labels=labels,
                git_events=git.get(member.id, 0),
                pod_role=role,
            ),
        )

    async def _title(self, tenant_id: str, member: GraphNode) -> str | None:
        """The directory's title (synced, never copied), else one the member node holds."""
        if self._directory is not None:
            user = await self._directory.get(tenant_id, member.id)
            if user is not None and user.title and user.title.strip():
                return user.title
        title = member.metadata.get("title")
        return title if isinstance(title, str) and title.strip() else None

    async def _issue_kinds(
        self, tenant_id: str, pod_id: str, member_id: str
    ) -> tuple[list[str], list[list[str]]]:
        """The types and labels of the person's issues in the pod's remit.

        Their assigned issues the pod holds (``pods_for_task``); when no issue
        names a pod at all (issues sit under projects), all their issues.
        """
        today = self._today()
        tasks: list[GraphNode] = []
        for edge in await self._graph.list_edges(
            tenant_id, from_node_id=member_id, kind=EdgeKind.ASSIGNED_TO
        ):
            if not edge.is_active_on(today):
                continue
            task = await self._graph.get_node(tenant_id, edge.to_node_id)
            if task is not None and task.kind is NodeKind.TASK:
                tasks.append(task)
        placed: list[GraphNode] = []
        anywhere = False
        for task in tasks:
            pods = {pod.id for pod in await self._graph.pods_for_task(tenant_id, task.id, today)}
            anywhere = anywhere or bool(pods)
            if pod_id in pods:
                placed.append(task)
        chosen = placed if anywhere else tasks
        types = [kind for task in chosen if (kind := _text(task.metadata.get("issue_type")))]
        labels = [
            [*_names(task.metadata.get("labels")), *_names(task.metadata.get("components"))]
            for task in chosen
        ]
        return types, labels

    async def _git_counts(self, tenant_id: str) -> dict[str, int]:
        """Commits and merge requests per member in the suggestion window."""
        if self._facts is None:
            return {}
        since = self._clock() - timedelta(days=SUGGESTION_WINDOW_DAYS)
        facts = await self._facts.list_recent_facts(
            tenant_id, since=since, sources=_GIT_SOURCES, limit=_GIT_SCAN_LIMIT
        )
        return _git_by_member(facts)


def _row_order(row: PodMemberProfile) -> tuple[date, datetime]:
    return (row.valid_from, row.set_at or datetime(1970, 1, 1, tzinfo=UTC))


def _git_by_member(facts: Iterable[FactEvent]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for fact in facts:
        author = (
            fact.entity_ref.id
            if fact.entity_ref.kind is NodeKind.DEVELOPER
            else fact.payload.get("author_id")
        )
        if isinstance(author, str) and author:
            counts[author] = counts.get(author, 0) + 1
    return counts


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _names(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    return [name.strip() for name in value.split(",") if name.strip()]
