from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime

from core.domain.directory import DirectoryUser
from core.domain.errors import GraphNotFound, OpenProgramError, ProviderUnavailable
from core.domain.escalation import (
    EscalationContact,
    EscalationTarget,
    PodEscalationContacts,
    apply_escalation_contacts_to_metadata,
    escalation_contacts_from_metadata,
    with_member_identity,
)
from core.domain.graph import (
    EdgeKind,
    FactEvent,
    GraphEdge,
    GraphNode,
    JsonScalar,
    NodeKind,
    WorkItem,
    workstreams_in_use,
)
from core.domain.identity import IdentityLink
from core.domain.rollup import Rag
from core.domain.status import CheckInPreference, StatusSource
from core.domain.writeback import WriteBackGate, WriteBackGateSource
from core.ports.directory import DirectoryUserRepository
from core.ports.issue_tracker import IssueTracker
from core.ports.repositories import (
    GraphRepository,
    IdentityLinkRepository,
    RollupRepository,
    StatusRepository,
    TimeSeriesRepository,
    WriteBackConfigRepository,
)


class ConfigValidationError(OpenProgramError):
    """Raised when a requested runtime config mutation is invalid."""


class ConfigConflict(OpenProgramError):
    """Raised when a requested runtime config mutation would duplicate state."""


# Metadata keys that hold a person's id rather than a value to show as is.
PERSON_METADATA_KEYS: tuple[str, ...] = ("owner_id", "tpm_id", "sm_id")


@dataclass(frozen=True, kw_only=True)
class DirectoryPersonView:
    """A person a node's metadata names, resolved to a member where one matches.

    ``id`` is the value exactly as stored, which may be a member node id or a
    chat user id. ``member_id`` and ``name`` are None when no member matches,
    so a reader can tell an unresolved id from a name.
    """

    key: str
    id: str
    member_id: str | None = None
    name: str | None = None


@dataclass(frozen=True, kw_only=True)
class DirectoryItemView:
    id: str
    kind: NodeKind
    name: str
    description: str | None
    code: str | None
    metadata: Mapping[str, JsonScalar]
    rag: Rag | None = None
    source: StatusSource | None = None
    program_ids: tuple[str, ...] = ()
    project_ids: tuple[str, ...] = ()
    workstream_ids: tuple[str, ...] = ()
    pod_ids: tuple[str, ...] = ()
    member_ids: tuple[str, ...] = ()
    task_ids: tuple[str, ...] = ()
    people: tuple[DirectoryPersonView, ...] = ()
    #: False only for a workstream holding no task or work item on the day
    #: (``workstreams_in_use``). The lists leave such a workstream out; only
    #: ``DirectoryService.get_workstream`` returns one, for a direct link.
    in_use: bool = True


@dataclass(frozen=True, kw_only=True)
class IdentityAutoMatchMember:
    id: str
    name: str
    filled: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class IdentityAutoMatchResult:
    updated_count: int
    members: tuple[IdentityAutoMatchMember, ...]


@dataclass(frozen=True, kw_only=True)
class UnmappedMember:
    id: str
    name: str
    missing: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class EscalationContactChoice:
    """One escalation rung as an admin set it.

    Normally a member, whose linked chat id and name are then stored. A bare chat
    id is accepted when it belongs to a member, or when it is the one already
    stored, so a contact saved before members could be picked survives an
    unrelated edit.
    """

    member_id: str | None = None
    chat_external_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class PodEscalationContactChoices:
    scrum_master: EscalationContactChoice | None = None
    manager: EscalationContactChoice | None = None


@dataclass(frozen=True, kw_only=True)
class EscalationCandidate:
    """A member an admin can pick as a pod escalation contact."""

    member_id: str
    name: str
    chat_user_id: str | None
    in_pod: bool
    pod_role: str | None = None


@dataclass(frozen=True, kw_only=True)
class _MemberIdentity:
    id: str
    name: str
    chat_user_id: str | None


class ConfigService:
    """Admin changes to the tenant graph, dated so earlier days read as they were.

    A link made here holds from today (``today``) and an unlink ends it today,
    so a read as of an earlier day, a past day's report among them, still sees
    the links of that day. Deleting a node ends its links today and keeps the
    node for those reads. Links stored without a start date (seeds, synced
    containers, links made before links were dated) hold on every earlier day.
    """

    def __init__(
        self,
        graph_repository: GraphRepository,
        status_repository: StatusRepository,
        directory_repository: DirectoryUserRepository | None = None,
        time_series_repository: TimeSeriesRepository | None = None,
        identity_link_repository: IdentityLinkRepository | None = None,
        writeback_config_repository: WriteBackConfigRepository | None = None,
        issue_tracker: IssueTracker | None = None,
        require_issue_tracker_link: bool = False,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._graph_repository = graph_repository
        self._status_repository = status_repository
        self._directory_repository = directory_repository
        self._time_series_repository = time_series_repository
        self._identity_link_repository = identity_link_repository
        self._writeback_config_repository = writeback_config_repository
        self._issue_tracker = issue_tracker
        # True when a real tracker is configured: a member it cannot attribute
        # issues to is then as unmapped as one who cannot be messaged.
        self._require_issue_tracker_link = require_issue_tracker_link
        # The day a link starts or ends: the day the config router has always
        # used (a pod membership's start) and the directory reads by default.
        self._today = today

    async def list_nodes(self, tenant_id: str, kind: NodeKind) -> list[GraphNode]:
        return await self._graph_repository.list_nodes(tenant_id, kind)

    async def get_node(self, tenant_id: str, id: str, kind: NodeKind) -> GraphNode:
        return await self._ensure_node(tenant_id, id, kind)

    async def create_node(
        self,
        tenant_id: str,
        kind: NodeKind,
        id: str,
        name: str,
        metadata: Mapping[str, JsonScalar] | None = None,
    ) -> GraphNode:
        normalized_id = _clean_required(id, "id")
        normalized_name = _clean_required(name, "name")
        existing = await self._graph_repository.get_node(tenant_id, normalized_id)
        if existing is not None:
            raise ConfigConflict(f"node {normalized_id} already exists")
        node = GraphNode(
            tenant_id=tenant_id,
            id=normalized_id,
            kind=kind,
            name=normalized_name,
            metadata=_metadata(metadata),
        )
        await self._graph_repository.upsert_node(node)
        return node

    async def update_node(
        self,
        tenant_id: str,
        id: str,
        kind: NodeKind,
        name: str | None = None,
        metadata: Mapping[str, JsonScalar] | None = None,
    ) -> GraphNode:
        existing = await self._ensure_node(tenant_id, id, kind)
        updated_metadata = dict(existing.metadata)
        if metadata is not None:
            updated_metadata.update(_metadata(metadata))
        updated = GraphNode(
            tenant_id=existing.tenant_id,
            id=existing.id,
            kind=existing.kind,
            name=_clean_required(name, "name") if name is not None else existing.name,
            metadata=updated_metadata,
        )
        await self._graph_repository.upsert_node(updated)
        return updated

    async def delete_node(self, tenant_id: str, id: str, kind: NodeKind) -> None:
        """Delete from today: its links end today, and earlier days still read it."""
        existing = await self._ensure_node(tenant_id, id, kind)
        await self._graph_repository.delete_node(existing.tenant_id, existing.id, on=self._today())
        if kind is NodeKind.DEVELOPER:
            await self._status_repository.delete_checkin_preference(tenant_id, id)

    async def list_edges(self, tenant_id: str) -> list[GraphEdge]:
        return await self._graph_repository.list_edges(tenant_id)

    async def link_program_project(
        self,
        tenant_id: str,
        program_id: str,
        project_id: str,
    ) -> GraphEdge:
        await self._ensure_node(tenant_id, program_id, NodeKind.PROGRAM)
        await self._ensure_node(tenant_id, project_id, NodeKind.PROJECT)
        return await self._add_unique_edge(
            tenant_id,
            program_id,
            project_id,
            EdgeKind.CONTAINS,
        )

    async def unlink_program_project(
        self,
        tenant_id: str,
        program_id: str | None,
        project_id: str,
    ) -> None:
        await self._ensure_node(tenant_id, project_id, NodeKind.PROJECT)
        edges = await self._graph_repository.list_edges(
            tenant_id,
            from_node_id=program_id,
            to_node_id=project_id,
            kind=EdgeKind.CONTAINS,
        )
        await self._end_links_from_kind(
            tenant_id,
            edges,
            from_kind=NodeKind.PROGRAM,
            not_found=f"program link for project {project_id} was not found",
        )

    async def link_project_pod(
        self,
        tenant_id: str,
        project_id: str,
        pod_id: str,
    ) -> GraphEdge:
        await self._ensure_node(tenant_id, project_id, NodeKind.PROJECT)
        await self._ensure_node(tenant_id, pod_id, NodeKind.POD)
        return await self._add_unique_edge(
            tenant_id,
            project_id,
            pod_id,
            EdgeKind.CONTAINS,
        )

    async def unlink_project_pod(
        self,
        tenant_id: str,
        project_id: str,
        pod_id: str,
    ) -> None:
        await self._ensure_node(tenant_id, project_id, NodeKind.PROJECT)
        await self._ensure_node(tenant_id, pod_id, NodeKind.POD)
        await self._end_link(tenant_id, project_id, pod_id, EdgeKind.CONTAINS)

    async def link_project_workstream(
        self,
        tenant_id: str,
        project_id: str,
        workstream_id: str,
    ) -> GraphEdge:
        await self._ensure_node(tenant_id, project_id, NodeKind.PROJECT)
        await self._ensure_node(tenant_id, workstream_id, NodeKind.WORKSTREAM)
        return await self._add_unique_edge(
            tenant_id,
            project_id,
            workstream_id,
            EdgeKind.CONTAINS,
        )

    async def unlink_project_workstream(
        self,
        tenant_id: str,
        project_id: str,
        workstream_id: str,
    ) -> None:
        await self._ensure_node(tenant_id, project_id, NodeKind.PROJECT)
        await self._ensure_node(tenant_id, workstream_id, NodeKind.WORKSTREAM)
        await self._end_link(tenant_id, project_id, workstream_id, EdgeKind.CONTAINS)

    async def assign_pod_workstream(
        self,
        tenant_id: str,
        pod_id: str,
        workstream_id: str,
    ) -> GraphEdge:
        await self._ensure_node(tenant_id, pod_id, NodeKind.POD)
        await self._ensure_node(tenant_id, workstream_id, NodeKind.WORKSTREAM)
        return await self._add_unique_edge(
            tenant_id,
            pod_id,
            workstream_id,
            EdgeKind.ASSIGNED_TO,
        )

    async def unassign_pod_workstream(
        self,
        tenant_id: str,
        pod_id: str,
        workstream_id: str,
    ) -> None:
        await self._ensure_node(tenant_id, pod_id, NodeKind.POD)
        await self._ensure_node(tenant_id, workstream_id, NodeKind.WORKSTREAM)
        await self._end_link(tenant_id, pod_id, workstream_id, EdgeKind.ASSIGNED_TO)

    async def link_workstream_task(
        self,
        tenant_id: str,
        workstream_id: str,
        task_id: str,
    ) -> GraphEdge:
        await self._ensure_node(tenant_id, workstream_id, NodeKind.WORKSTREAM)
        await self._ensure_node(tenant_id, task_id, NodeKind.TASK)
        return await self._add_unique_edge(
            tenant_id,
            workstream_id,
            task_id,
            EdgeKind.CONTAINS,
        )

    async def unlink_workstream_task(
        self,
        tenant_id: str,
        workstream_id: str,
        task_id: str,
    ) -> None:
        await self._ensure_node(tenant_id, workstream_id, NodeKind.WORKSTREAM)
        await self._ensure_node(tenant_id, task_id, NodeKind.TASK)
        await self._end_link(tenant_id, workstream_id, task_id, EdgeKind.CONTAINS)

    async def link_pod_member(
        self,
        tenant_id: str,
        pod_id: str,
        member_id: str,
        role: str,
        valid_from: date | None = None,
    ) -> GraphEdge:
        """Add the member to the pod from ``valid_from``, today when not given."""
        await self._ensure_node(tenant_id, pod_id, NodeKind.POD)
        await self._ensure_node(tenant_id, member_id, NodeKind.DEVELOPER)
        return await self._add_unique_edge(
            tenant_id,
            pod_id,
            member_id,
            EdgeKind.CONTAINS,
            valid_from=valid_from,
            metadata={"role": _clean_required(role, "role")},
        )

    async def create_work_item(
        self,
        tenant_id: str,
        id: str,
        name: str,
        metadata: Mapping[str, JsonScalar] | None = None,
    ) -> GraphNode:
        normalized_id = _clean_required(id, "id")
        normalized_name = _clean_required(name, "name")
        existing = await self._graph_repository.get_node(tenant_id, normalized_id)
        if existing is not None:
            raise ConfigConflict(f"node {normalized_id} already exists")
        node = WorkItem(
            tenant_id=tenant_id,
            id=normalized_id,
            name=normalized_name,
            metadata=_work_item_metadata(metadata),
        )
        await self._graph_repository.upsert_node(node)
        return node

    async def create_work_item_from_branch(
        self,
        tenant_id: str,
        repo: str,
        branch: str,
        name: str | None = None,
        metadata: Mapping[str, JsonScalar] | None = None,
    ) -> GraphNode:
        branch_name = _clean_required(branch, "branch")
        derived_id = f"branch-{_slugify_identifier(repo)}-{_slugify_identifier(branch_name)}"
        merged_metadata = dict(metadata or {})
        merged_metadata.setdefault("repo", repo)
        merged_metadata.setdefault("branch", branch_name)
        merged_metadata.setdefault("item_type", "feature")
        merged_metadata.setdefault("state", "proposed")
        return await self.create_work_item(
            tenant_id,
            derived_id,
            name or branch_name,
            merged_metadata,
        )

    async def create_work_item_from_pr(
        self,
        tenant_id: str,
        repo: str,
        pr_id: str,
        title: str,
        metadata: Mapping[str, JsonScalar] | None = None,
    ) -> GraphNode:
        normalized_pr_id = _clean_required(pr_id, "pr_id")
        derived_id = f"pr-{_slugify_identifier(repo)}-{_slugify_identifier(normalized_pr_id)}"
        merged_metadata = dict(metadata or {})
        merged_metadata.setdefault("repo", repo)
        merged_metadata.setdefault("pr_id", normalized_pr_id)
        merged_metadata.setdefault("item_type", "feature")
        merged_metadata.setdefault("state", "proposed")
        return await self.create_work_item(tenant_id, derived_id, title, merged_metadata)

    async def transition_work_item(
        self,
        tenant_id: str,
        id: str,
        new_state: str,
    ) -> GraphNode:
        existing = await self._ensure_node(tenant_id, id, NodeKind.WORK_ITEM)
        updated_metadata = dict(existing.metadata)
        normalized_state = _clean_required(new_state, "new_state")
        previous_state = updated_metadata.get("state")
        transition_at = datetime.now(tz=UTC)
        updated_metadata["state"] = normalized_state
        updated_metadata["last_transition_at"] = transition_at.isoformat()
        updated = WorkItem(
            tenant_id=existing.tenant_id,
            id=existing.id,
            name=existing.name,
            metadata=updated_metadata,
        )
        await self._graph_repository.upsert_node(updated)
        await self._time_series_repository_or_raise().append_fact_once(
            FactEvent(
                tenant_id=tenant_id,
                source="work_item",
                entity_ref=updated.ref,
                payload={
                    "work_item_id": updated.id,
                    "name": updated.name,
                    "from_state": previous_state if isinstance(previous_state, str) else None,
                    "to_state": normalized_state,
                    "item_type": updated_metadata.get("item_type"),
                    "repo": updated_metadata.get("repo"),
                    "branch": updated_metadata.get("branch"),
                    "pr_id": updated_metadata.get("pr_id"),
                },
                observed_at=transition_at,
                correlation_id=f"work_item:{tenant_id}:{updated.id}:{normalized_state}:{updated_metadata['last_transition_at']}",
            )
        )
        return updated

    async def link_work_item_to_workstream(
        self,
        tenant_id: str,
        workstream_id: str,
        work_item_id: str,
    ) -> GraphEdge:
        await self._ensure_node(tenant_id, workstream_id, NodeKind.WORKSTREAM)
        await self._ensure_node(tenant_id, work_item_id, NodeKind.WORK_ITEM)
        return await self._add_unique_edge(
            tenant_id,
            workstream_id,
            work_item_id,
            EdgeKind.CONTAINS,
        )

    async def unlink_work_item_from_workstream(
        self,
        tenant_id: str,
        workstream_id: str,
        work_item_id: str,
    ) -> None:
        await self._ensure_node(tenant_id, workstream_id, NodeKind.WORKSTREAM)
        await self._ensure_node(tenant_id, work_item_id, NodeKind.WORK_ITEM)
        await self._end_link(tenant_id, workstream_id, work_item_id, EdgeKind.CONTAINS)

    async def unlink_pod_member(self, tenant_id: str, pod_id: str, member_id: str) -> None:
        await self._ensure_node(tenant_id, pod_id, NodeKind.POD)
        await self._ensure_node(tenant_id, member_id, NodeKind.DEVELOPER)
        await self._end_link(tenant_id, pod_id, member_id, EdgeKind.CONTAINS)

    async def assign_member_task(
        self,
        tenant_id: str,
        member_id: str,
        task_id: str,
    ) -> GraphEdge:
        await self._ensure_node(tenant_id, member_id, NodeKind.DEVELOPER)
        await self._ensure_node(tenant_id, task_id, NodeKind.TASK)
        return await self._add_unique_edge(
            tenant_id,
            member_id,
            task_id,
            EdgeKind.ASSIGNED_TO,
        )

    async def unassign_member_task(
        self,
        tenant_id: str,
        member_id: str,
        task_id: str,
    ) -> None:
        await self._ensure_node(tenant_id, member_id, NodeKind.DEVELOPER)
        await self._ensure_node(tenant_id, task_id, NodeKind.TASK)
        await self._end_link(tenant_id, member_id, task_id, EdgeKind.ASSIGNED_TO)

    async def get_checkin_preference(
        self,
        tenant_id: str,
        member_id: str,
    ) -> CheckInPreference | None:
        await self._ensure_node(tenant_id, member_id, NodeKind.DEVELOPER)
        return await self._status_repository.checkin_preference_for(tenant_id, member_id)

    async def record_checkin_preference(self, preference: CheckInPreference) -> CheckInPreference:
        await self._ensure_node(preference.tenant_id, preference.developer_id, NodeKind.DEVELOPER)
        await self._status_repository.record_checkin_preference(preference)
        return preference

    async def list_checkin_preferences(self, tenant_id: str) -> list[CheckInPreference]:
        return await self._status_repository.list_checkin_preferences(tenant_id)

    async def get_identity_link(
        self,
        tenant_id: str,
        member_id: str,
    ) -> IdentityLink | None:
        await self._ensure_node(tenant_id, member_id, NodeKind.DEVELOPER)
        repository = self._identity_link_repository_or_raise()
        return await repository.get_identity_link(tenant_id, member_id)

    async def set_identity_link(self, link: IdentityLink) -> IdentityLink:
        await self._ensure_node(link.tenant_id, link.developer_id, NodeKind.DEVELOPER)
        repository = self._identity_link_repository_or_raise()
        await repository.upsert_identity_link(link)
        return link

    async def auto_match_identity_links(self, tenant_id: str) -> IdentityAutoMatchResult:
        """Pre-populate missing identity-link fields from directory data.

        For every configured developer node, resolve the matching directory user
        (the member id is the directory ``external_id``) and fill any field that
        is currently unset: ``external_id`` -> ``chat_user_id`` and ``email`` ->
        ``jira_email``. Then, when an issue tracker is wired, resolve
        ``jira_email`` -> ``jira_account_id``: the account id is what the tracker
        indexes assignments by, so without it the member's issues are never
        found. An admin-entered ``jira_email`` (someone whose tracker address
        differs from their chat address) is resolved the same way. Admin-set
        values are never overwritten.
        """
        directory = self._directory_repository_or_raise()
        repository = self._identity_link_repository_or_raise()
        members = await self._graph_repository.list_nodes(tenant_id, NodeKind.DEVELOPER)
        matched: list[IdentityAutoMatchMember] = []
        for member in members:
            directory_user = await directory.get(tenant_id, member.id)
            if directory_user is None:
                continue
            existing = await repository.get_identity_link(tenant_id, member.id)
            link = existing or IdentityLink(tenant_id=tenant_id, developer_id=member.id)
            filled: list[str] = []
            chat_user_id = link.chat_user_id
            jira_email = link.jira_email
            if chat_user_id is None and directory_user.external_id:
                chat_user_id = directory_user.external_id
                filled.append("chat_user_id")
            if jira_email is None and directory_user.email:
                jira_email = directory_user.email
                filled.append("jira_email")
            jira_account_id = link.jira_account_id
            if jira_account_id is None and jira_email is not None:
                jira_account_id = await self._tracker_account_for(tenant_id, jira_email)
                if jira_account_id is not None:
                    filled.append("jira_account_id")
            if not filled:
                continue
            await repository.upsert_identity_link(
                replace(
                    link,
                    chat_user_id=chat_user_id,
                    jira_email=jira_email,
                    jira_account_id=jira_account_id,
                )
            )
            matched.append(
                IdentityAutoMatchMember(id=member.id, name=member.name, filled=tuple(filled))
            )
        return IdentityAutoMatchResult(updated_count=len(matched), members=tuple(matched))

    async def _tracker_account_for(self, tenant_id: str, email: str) -> str | None:
        if self._issue_tracker is None:
            return None
        try:
            found = await self._issue_tracker.find_user_by_email(tenant_id, email)
        except ProviderUnavailable:
            return None
        return found.external_id if found is not None else None

    async def list_unmapped_members(self, tenant_id: str) -> list[UnmappedMember]:
        """List developer nodes whose identity link cannot reach or attribute them.

        A member with no resolved ``chat_user_id`` cannot receive check-in DMs.
        With a real issue tracker configured, a member with no
        ``jira_account_id`` is unmapped too: the tracker's issues can never be
        attributed to them, so their status is built without their work. Each
        is surfaced to admins together with the identity fields still unset.
        """
        repository = self._identity_link_repository_or_raise()
        members = await self._graph_repository.list_nodes(tenant_id, NodeKind.DEVELOPER)
        links = {
            link.developer_id: link for link in await repository.list_identity_links(tenant_id)
        }
        unmapped: list[UnmappedMember] = []
        for member in members:
            link = links.get(member.id)
            if (
                link is not None
                and link.chat_user_id is not None
                and not (self._require_issue_tracker_link and link.jira_account_id is None)
            ):
                continue
            unmapped.append(
                UnmappedMember(
                    id=member.id,
                    name=member.name,
                    missing=_missing_identity_fields(link),
                )
            )
        return unmapped

    async def get_tenant_writeback(self, tenant_id: str, default: bool) -> WriteBackGate:
        """Resolve the system gate: persisted tenant override, else the fallback."""
        repository = self._writeback_config_repository_or_raise()
        override = await repository.get_writeback_enabled(tenant_id)
        if override is None:
            return WriteBackGate(enabled=default, source=WriteBackGateSource.DEFAULT)
        return WriteBackGate(enabled=override, source=WriteBackGateSource.TENANT)

    async def set_tenant_writeback_enabled(self, tenant_id: str, enabled: bool) -> WriteBackGate:
        repository = self._writeback_config_repository_or_raise()
        await repository.set_writeback_enabled(tenant_id, enabled)
        return WriteBackGate(enabled=enabled, source=WriteBackGateSource.TENANT)

    async def get_pod_escalation_contacts(
        self, tenant_id: str, pod_id: str
    ) -> PodEscalationContacts:
        """Stored contacts, each resolved to the member it reaches now.

        ``member_id`` on a returned contact is None when its chat id belongs to
        no member. Reading never rewrites what is stored.
        """
        node = await self._ensure_node(tenant_id, pod_id, NodeKind.POD)
        members = await self._member_identities(tenant_id)
        return _current_contacts(escalation_contacts_from_metadata(node.metadata), members)

    async def set_pod_escalation_contacts(
        self, tenant_id: str, pod_id: str, choices: PodEscalationContactChoices
    ) -> PodEscalationContacts:
        node = await self._ensure_node(tenant_id, pod_id, NodeKind.POD)
        stored = escalation_contacts_from_metadata(node.metadata)
        members = await self._member_identities(tenant_id)
        contacts = PodEscalationContacts(
            scrum_master=_chosen_contact(
                EscalationTarget.SCRUM_MASTER,
                choices.scrum_master,
                stored.scrum_master,
                members,
            ),
            manager=_chosen_contact(
                EscalationTarget.MANAGER, choices.manager, stored.manager, members
            ),
        )
        metadata = dict(node.metadata)
        apply_escalation_contacts_to_metadata(metadata, contacts)
        await self._graph_repository.upsert_node(replace(node, metadata=metadata))
        return _current_contacts(contacts, members)

    async def list_escalation_candidates(
        self, tenant_id: str, pod_id: str, as_of: date
    ) -> list[EscalationCandidate]:
        """Every member, the pod's own first, with the chat id their link holds."""
        await self._ensure_node(tenant_id, pod_id, NodeKind.POD)
        members = await self._member_identities(tenant_id)
        roles: dict[str, str | None] = {}
        for edge in await self._graph_repository.list_edges(
            tenant_id, from_node_id=pod_id, kind=EdgeKind.CONTAINS
        ):
            if edge.to_node_id in members and edge.is_active_on(as_of):
                roles[edge.to_node_id] = _clean_optional(edge.metadata.get("role"))
        candidates = [
            EscalationCandidate(
                member_id=member.id,
                name=member.name,
                chat_user_id=member.chat_user_id,
                in_pod=member.id in roles,
                pod_role=roles.get(member.id),
            )
            for member in members.values()
        ]
        return sorted(
            candidates,
            key=lambda candidate: (
                not candidate.in_pod,
                candidate.name.lower(),
                candidate.member_id,
            ),
        )

    async def search_directory(
        self,
        tenant_id: str,
        query: str = "",
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[list[DirectoryUser], int]:
        repository = self._directory_repository_or_raise()
        users = await repository.search(tenant_id, query=query, limit=limit, offset=offset)
        count = await repository.count(tenant_id, query=query)
        return users, count

    async def add_member_from_directory(self, tenant_id: str, external_id: str) -> GraphNode:
        members = await self.add_members_from_directory(tenant_id, [external_id])
        return members[0]

    async def add_members_from_directory(
        self, tenant_id: str, external_ids: Sequence[str]
    ) -> list[GraphNode]:
        repository = self._directory_repository_or_raise()
        directory_users: dict[str, DirectoryUser] = {}
        existing_nodes: dict[str, GraphNode] = {}
        for external_id in external_ids:
            directory_user = await repository.get(tenant_id, external_id)
            if directory_user is None or not directory_user.is_active:
                raise GraphNotFound(
                    f"active directory user {external_id} not found for tenant {tenant_id}"
                )
            existing = await self._graph_repository.get_node(tenant_id, external_id)
            if existing is not None:
                if existing.kind is not NodeKind.DEVELOPER:
                    raise ConfigConflict(
                        f"{external_id} exists as a {existing.kind.value}, not a developer"
                    )
                existing_nodes[external_id] = existing
            directory_users[external_id] = directory_user

        created_nodes: dict[str, GraphNode] = {}
        members: list[GraphNode] = []
        for external_id in external_ids:
            existing = existing_nodes.get(external_id)
            if existing is not None:
                members.append(existing)
                continue
            created = created_nodes.get(external_id)
            if created is None:
                directory_user = directory_users[external_id]
                created = GraphNode(
                    tenant_id=tenant_id,
                    id=external_id,
                    kind=NodeKind.DEVELOPER,
                    name=directory_user.display_name,
                    metadata=_directory_metadata(directory_user),
                )
                await self._graph_repository.upsert_node(created)
                created_nodes[external_id] = created
            members.append(created)
        return members

    async def _ensure_node(
        self,
        tenant_id: str,
        id: str,
        kind: NodeKind,
    ) -> GraphNode:
        node = await self._graph_repository.get_node(tenant_id, id)
        if node is None:
            raise GraphNotFound(f"{kind.value} {id} not found for tenant {tenant_id}")
        if node.kind is not kind:
            raise GraphNotFound(f"{id} exists as a {node.kind.value}, not a {kind.value}")
        return node

    async def _add_unique_edge(
        self,
        tenant_id: str,
        from_node_id: str,
        to_node_id: str,
        kind: EdgeKind,
        *,
        valid_from: date | None = None,
        metadata: Mapping[str, JsonScalar] | None = None,
    ) -> GraphEdge:
        """Link the two nodes from today, unless they are linked already.

        Only a link still in force counts as one: a link ended earlier is
        history, so linking again adds a new one from today, and the days in
        between keep reading as unlinked.
        """
        if from_node_id == to_node_id:
            raise ConfigValidationError("self links are not allowed")
        today = self._today()
        existing = [
            edge
            for edge in await self._graph_repository.list_edges(
                tenant_id,
                from_node_id=from_node_id,
                to_node_id=to_node_id,
                kind=kind,
            )
            if edge.ends_after(today)
        ]
        if existing:
            raise ConfigConflict(f"{kind.value} link already exists")
        edge = GraphEdge(
            tenant_id=tenant_id,
            from_node_id=from_node_id,
            to_node_id=to_node_id,
            kind=kind,
            valid_from=valid_from if valid_from is not None else today,
            metadata=_metadata(metadata),
        )
        await self._graph_repository.add_edge(edge)
        return edge

    async def _end_link(
        self,
        tenant_id: str,
        from_node_id: str,
        to_node_id: str,
        kind: EdgeKind,
    ) -> None:
        """End today every link still in force between the two nodes."""
        edges = await self._graph_repository.list_edges(
            tenant_id,
            from_node_id=from_node_id,
            to_node_id=to_node_id,
            kind=kind,
        )
        await self._end_links(edges, not_found=f"{kind.value} link was not found")

    async def _end_links_from_kind(
        self,
        tenant_id: str,
        edges: list[GraphEdge],
        *,
        from_kind: NodeKind,
        not_found: str,
    ) -> None:
        matching: list[GraphEdge] = []
        for edge in edges:
            from_node = await self._graph_repository.get_node(tenant_id, edge.from_node_id)
            if from_node is not None and from_node.kind is from_kind:
                matching.append(edge)
        await self._end_links(matching, not_found=not_found)

    async def _end_links(self, edges: Sequence[GraphEdge], *, not_found: str) -> None:
        """End the links still in force today; earlier days keep reading them.

        A link that already ended is history and is left alone, so with none
        still in force there is nothing to unlink.
        """
        today = self._today()
        current = [edge for edge in edges if edge.ends_after(today)]
        if not current:
            raise GraphNotFound(not_found)
        for edge in current:
            await self._graph_repository.end_edge(edge, today)

    def _directory_repository_or_raise(self) -> DirectoryUserRepository:
        if self._directory_repository is None:
            raise ConfigValidationError("directory repository is not configured")
        return self._directory_repository

    def _identity_link_repository_or_raise(self) -> IdentityLinkRepository:
        if self._identity_link_repository is None:
            raise ConfigValidationError("identity link repository is not configured")
        return self._identity_link_repository

    async def _member_identities(self, tenant_id: str) -> dict[str, _MemberIdentity]:
        """Every configured member with the chat id its identity link holds."""
        links: dict[str, IdentityLink] = {}
        if self._identity_link_repository is not None:
            links = {
                link.developer_id: link
                for link in await self._identity_link_repository.list_identity_links(tenant_id)
            }
        members = await self._graph_repository.list_nodes(tenant_id, NodeKind.DEVELOPER)
        identities: dict[str, _MemberIdentity] = {}
        for member in sorted(members, key=lambda node: node.id):
            link = links.get(member.id)
            identities[member.id] = _MemberIdentity(
                id=member.id,
                name=member.name,
                chat_user_id=_clean_optional(link.chat_user_id if link else None),
            )
        return identities

    def _writeback_config_repository_or_raise(self) -> WriteBackConfigRepository:
        if self._writeback_config_repository is None:
            raise ConfigValidationError("writeback config repository is not configured")
        return self._writeback_config_repository

    def _time_series_repository_or_raise(self) -> TimeSeriesRepository:
        if self._time_series_repository is None:
            raise ConfigValidationError("time series repository is not configured")
        return self._time_series_repository


class DirectoryService:
    """The directory every role reads: programs, projects, workstreams and pods.

    Workstreams are optional (``workstreams_in_use``): the lists hold only the
    ones in use on the day, and every item's ``workstream_ids`` names only
    those, so an empty workstream is in no navigator, heat row, palette or link
    chip. A direct read (``get_workstream``) still opens an empty one, marked
    ``in_use=False``. The config API lists every workstream, empty or not.
    """

    def __init__(
        self,
        graph_repository: GraphRepository,
        rollup_repository: RollupRepository,
        identity_link_repository: IdentityLinkRepository | None = None,
    ) -> None:
        self._graph_repository = graph_repository
        self._rollup_repository = rollup_repository
        self._identity_link_repository = identity_link_repository

    async def list_programs(self, tenant_id: str, as_of: date) -> list[DirectoryItemView]:
        return await self._list_items(tenant_id, NodeKind.PROGRAM, as_of)

    async def list_projects(self, tenant_id: str, as_of: date) -> list[DirectoryItemView]:
        return await self._list_items(tenant_id, NodeKind.PROJECT, as_of)

    async def list_workstreams(self, tenant_id: str, as_of: date) -> list[DirectoryItemView]:
        """The workstreams in use on ``as_of``; an empty one is left out."""
        return await self._list_items(tenant_id, NodeKind.WORKSTREAM, as_of)

    async def get_workstream(
        self,
        tenant_id: str,
        workstream_id: str,
        as_of: date,
    ) -> DirectoryItemView:
        """One workstream, in use or not: a direct link to an empty one still opens."""
        await self._ensure_node(tenant_id, workstream_id, NodeKind.WORKSTREAM, as_of)
        items = await self._list_items(tenant_id, NodeKind.WORKSTREAM, as_of, only_id=workstream_id)
        if not items:
            raise GraphNotFound(f"workstream {workstream_id} not found for tenant {tenant_id}")
        return items[0]

    async def list_project_workstreams(
        self,
        tenant_id: str,
        project_id: str,
        as_of: date,
    ) -> list[DirectoryItemView]:
        await self._ensure_node(tenant_id, project_id, NodeKind.PROJECT, as_of)
        return [
            item
            for item in await self.list_workstreams(tenant_id, as_of)
            if project_id in item.project_ids
        ]

    async def list_pods(self, tenant_id: str, as_of: date) -> list[DirectoryItemView]:
        return await self._list_items(tenant_id, NodeKind.POD, as_of)

    async def _list_items(
        self,
        tenant_id: str,
        kind: NodeKind,
        as_of: date,
        *,
        only_id: str | None = None,
    ) -> list[DirectoryItemView]:
        """The items of ``kind`` as of ``as_of``; ``only_id`` reads that one, in use or not.

        Nodes and links are read as they were that day: a node deleted later is
        still listed, and a link ended later or made later reads as it stood.
        """
        nodes = await self._graph_repository.list_nodes(tenant_id, as_of=as_of)
        node_by_id = {node.id: node for node in nodes}
        edges = [
            edge
            for edge in await self._graph_repository.list_edges(tenant_id)
            if edge.is_active_on(as_of)
        ]
        in_use = workstreams_in_use(nodes, edges, as_of)
        selected = [
            node
            for node in nodes
            if node.kind is kind
            and (node.id == only_id if only_id is not None else _shown(node, in_use))
        ]
        # An empty workstream is nobody's link: only the ones in use are named.
        shown_by_id = {
            node_id: node for node_id, node in node_by_id.items() if _shown(node, in_use)
        }
        status_by_ref = {
            (status.entity_ref.kind, status.entity_ref.id): status
            for status in await self._rollup_repository.list_node_statuses(tenant_id, as_of)
        }
        member_for = await self._member_resolver(tenant_id, nodes, selected)
        views: list[DirectoryItemView] = []
        for node in selected:
            status = status_by_ref.get((node.kind, node.id))
            outgoing = [
                edge
                for edge in edges
                if edge.from_node_id == node.id and edge.kind is EdgeKind.CONTAINS
            ]
            incoming = [
                edge
                for edge in edges
                if edge.to_node_id == node.id and edge.kind is EdgeKind.CONTAINS
            ]
            assignments = [
                edge
                for edge in edges
                if edge.from_node_id == node.id and edge.kind is EdgeKind.ASSIGNED_TO
            ]
            incoming_assignments = [
                edge
                for edge in edges
                if edge.to_node_id == node.id and edge.kind is EdgeKind.ASSIGNED_TO
            ]
            views.append(
                DirectoryItemView(
                    id=node.id,
                    kind=node.kind,
                    name=node.name,
                    description=_string_metadata(node, "description"),
                    code=_string_metadata(node, "code"),
                    metadata=node.metadata,
                    rag=status.rag if status else None,
                    source=status.source if status else None,
                    program_ids=_source_ids(incoming, node_by_id, NodeKind.PROGRAM),
                    project_ids=tuple(
                        dict.fromkeys(
                            (
                                *_source_ids(incoming, node_by_id, NodeKind.PROJECT),
                                *_target_ids(outgoing, node_by_id, NodeKind.PROJECT),
                            )
                        )
                    ),
                    workstream_ids=tuple(
                        dict.fromkeys(
                            (
                                *_source_ids(incoming, shown_by_id, NodeKind.WORKSTREAM),
                                *_target_ids(outgoing, shown_by_id, NodeKind.WORKSTREAM),
                                *_source_ids(
                                    incoming_assignments,
                                    shown_by_id,
                                    NodeKind.WORKSTREAM,
                                ),
                                *_target_ids(assignments, shown_by_id, NodeKind.WORKSTREAM),
                            )
                        )
                    ),
                    pod_ids=tuple(
                        dict.fromkeys(
                            (
                                *_source_ids(incoming, node_by_id, NodeKind.POD),
                                *_target_ids(outgoing, node_by_id, NodeKind.POD),
                                *_source_ids(incoming_assignments, node_by_id, NodeKind.POD),
                            )
                        )
                    ),
                    member_ids=_target_ids(outgoing, node_by_id, NodeKind.DEVELOPER),
                    task_ids=tuple(
                        dict.fromkeys(
                            (
                                *_target_ids(outgoing, node_by_id, NodeKind.TASK),
                                *_target_ids(assignments, node_by_id, NodeKind.TASK),
                            )
                        )
                    ),
                    people=_people(node, member_for),
                    in_use=_shown(node, in_use),
                )
            )
        return sorted(views, key=lambda item: (item.name, item.id))

    async def _member_resolver(
        self,
        tenant_id: str,
        nodes: Sequence[GraphNode],
        selected: Sequence[GraphNode],
    ) -> Mapping[str, GraphNode]:
        """Index members by every id a person field may hold.

        Admins type these fields by hand, so a value is either a member node id
        or the person's chat user id. Members imported from the chat directory
        share the two; members created by hand do not, which is why the chat id
        is looked up as well. The member list itself is admin-only, so this is
        the one place every role reading the directory gets the names.
        """
        wanted = {person_id for node in selected for _, person_id in _person_fields(node)}
        if not wanted:
            return {}
        members = {node.id: node for node in nodes if node.kind is NodeKind.DEVELOPER}
        by_id: dict[str, GraphNode] = {}
        if self._identity_link_repository is not None and not wanted.issubset(members):
            for link in await self._identity_link_repository.list_identity_links(tenant_id):
                member = members.get(link.developer_id)
                if member is not None and link.chat_user_id:
                    by_id.setdefault(link.chat_user_id, member)
        for member in members.values():
            chat_id = _string_metadata(member, "chat_external_id")
            if chat_id is not None:
                by_id.setdefault(chat_id, member)
        # A member's own node id always wins over another member's chat id.
        by_id.update(members)
        return by_id

    async def _ensure_node(
        self,
        tenant_id: str,
        id: str,
        kind: NodeKind,
        as_of: date,
    ) -> GraphNode:
        node = await self._graph_repository.get_node(tenant_id, id, as_of=as_of)
        if node is None:
            raise GraphNotFound(f"{kind.value} {id} not found for tenant {tenant_id}")
        if node.kind is not kind:
            raise GraphNotFound(f"{id} exists as a {node.kind.value}, not a {kind.value}")
        return node


def _shown(node: GraphNode, in_use: frozenset[str]) -> bool:
    """Whether the directory shows a node: anything but a workstream not in use."""
    return node.kind is not NodeKind.WORKSTREAM or node.id in in_use


def _person_fields(node: GraphNode) -> tuple[tuple[str, str], ...]:
    return tuple(
        (key, value)
        for key in PERSON_METADATA_KEYS
        if isinstance(value := node.metadata.get(key), str) and value
    )


def _people(
    node: GraphNode,
    member_for: Mapping[str, GraphNode],
) -> tuple[DirectoryPersonView, ...]:
    people: list[DirectoryPersonView] = []
    for key, person_id in _person_fields(node):
        member = member_for.get(person_id)
        people.append(
            DirectoryPersonView(
                key=key,
                id=person_id,
                member_id=member.id if member is not None else None,
                name=member.name if member is not None else None,
            )
        )
    return tuple(people)


def _source_ids(
    edges: list[GraphEdge],
    node_by_id: Mapping[str, GraphNode],
    kind: NodeKind,
) -> tuple[str, ...]:
    ids: list[str] = []
    for edge in edges:
        node = node_by_id.get(edge.from_node_id)
        if node is not None and node.kind is kind and node.id not in ids:
            ids.append(node.id)
    return tuple(sorted(ids))


def _target_ids(
    edges: list[GraphEdge],
    node_by_id: Mapping[str, GraphNode],
    kind: NodeKind,
) -> tuple[str, ...]:
    ids: list[str] = []
    for edge in edges:
        node = node_by_id.get(edge.to_node_id)
        if node is not None and node.kind is kind and node.id not in ids:
            ids.append(node.id)
    return tuple(sorted(ids))


def _member_with_chat_id(
    members: Mapping[str, _MemberIdentity], chat_external_id: str
) -> _MemberIdentity | None:
    for member in members.values():
        if member.chat_user_id == chat_external_id:
            return member
    return None


def _current_contact(
    contact: EscalationContact | None, members: Mapping[str, _MemberIdentity]
) -> EscalationContact | None:
    """Resolve a stored contact to the member it reaches now, if any."""
    if contact is None:
        return None
    member = members.get(contact.member_id) if contact.member_id is not None else None
    if member is None or member.chat_user_id is None:
        member = _member_with_chat_id(members, contact.chat_external_id)
    if member is None:
        return replace(contact, member_id=None)
    return with_member_identity(
        replace(contact, member_id=member.id),
        member_name=member.name,
        chat_user_id=member.chat_user_id,
    )


def _current_contacts(
    contacts: PodEscalationContacts, members: Mapping[str, _MemberIdentity]
) -> PodEscalationContacts:
    return PodEscalationContacts(
        scrum_master=_current_contact(contacts.scrum_master, members),
        manager=_current_contact(contacts.manager, members),
    )


def _chosen_contact(
    target: EscalationTarget,
    choice: EscalationContactChoice | None,
    stored: EscalationContact | None,
    members: Mapping[str, _MemberIdentity],
) -> EscalationContact | None:
    if choice is None:
        return None
    member_id = _clean_optional(choice.member_id)
    if member_id is not None:
        member = members.get(member_id)
        if member is None:
            raise GraphNotFound(f"member {member_id} was not found")
        if member.chat_user_id is None:
            raise ConfigValidationError(f"{member.name} has no chat ID linked")
        return EscalationContact(
            target=target,
            chat_external_id=member.chat_user_id,
            display_name=member.name,
            member_id=member.id,
        )
    chat_external_id = _clean_optional(choice.chat_external_id)
    if chat_external_id is None:
        raise ConfigValidationError("an escalation contact needs a member")
    member = _member_with_chat_id(members, chat_external_id)
    if member is not None:
        return EscalationContact(
            target=target,
            chat_external_id=chat_external_id,
            display_name=member.name,
            member_id=member.id,
        )
    if stored is not None and stored.chat_external_id == chat_external_id:
        # Saved before contacts were picked from members: keep it as it was.
        return stored
    raise ConfigValidationError(f"chat ID {chat_external_id} is not linked to any member")


def _clean_optional(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _missing_identity_fields(link: IdentityLink | None) -> tuple[str, ...]:
    if link is None:
        return ("chat_user_id", "jira_account_id", "jira_email", "vcs_username")
    fields = ("chat_user_id", "jira_account_id", "jira_email", "vcs_username")
    return tuple(field for field in fields if getattr(link, field) is None)


def _metadata(metadata: Mapping[str, JsonScalar] | None) -> dict[str, JsonScalar]:
    if metadata is None:
        return {}
    return {
        key: value
        for key, value in metadata.items()
        if isinstance(key, str) and (value is None or isinstance(value, str | int | float | bool))
    }


def _work_item_metadata(metadata: Mapping[str, JsonScalar] | None) -> dict[str, JsonScalar]:
    merged = _metadata(metadata)
    merged.setdefault("state", "proposed")
    merged.setdefault("item_type", "feature")
    merged.setdefault("created_at", datetime.now(tz=UTC).isoformat())
    return merged


def _clean_required(value: str, field: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ConfigValidationError(f"{field} must not be empty")
    return cleaned


def _slugify_identifier(value: str) -> str:
    cleaned = value.strip().lower()
    result: list[str] = []
    previous_dash = False
    for char in cleaned:
        if char.isalnum():
            result.append(char)
            previous_dash = False
        elif not previous_dash:
            result.append("-")
            previous_dash = True
    slug = "".join(result).strip("-")
    return slug or "item"


def _string_metadata(node: GraphNode, key: str) -> str | None:
    value = node.metadata.get(key)
    return value if isinstance(value, str) and value else None


def _directory_metadata(user: object) -> dict[str, JsonScalar]:
    metadata: dict[str, JsonScalar] = {}
    for key in ("email", "handle", "avatar_url", "source"):
        value = getattr(user, key, None)
        if isinstance(value, str) and value:
            metadata[key if key != "source" else "source"] = value
    external_id = getattr(user, "external_id", None)
    if isinstance(external_id, str) and external_id:
        metadata["chat_external_id"] = external_id
    return metadata
