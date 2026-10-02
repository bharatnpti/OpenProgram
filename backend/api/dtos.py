from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.application.ask_service import AskResponseView
from core.application.config_service import (
    DirectoryItemView,
    DirectoryPersonView,
    EscalationCandidate,
    EscalationContactChoice,
    IdentityAutoMatchResult,
    PodEscalationContactChoices,
    UnmappedMember,
)
from core.application.flow_metrics_service import (
    PortfolioFlowView,
    WorkItemFlowView,
    WorkstreamFlowSummaryView,
    WorkstreamFlowView,
)
from core.application.persona_views import (
    BlockerDetailView,
    BlockerView,
    CheckinDeveloperView,
    FocusItemView,
    FocusTaskView,
    FocusView,
    HeatmapCellView,
    NodeTrendView,
    PodBlockersView,
    PodCheckinsView,
    PodRollupView,
    PodTaskBlockerView,
    PodTaskOwnerView,
    PodTasksView,
    PodTaskView,
    PortfolioHeatmapView,
    ProgramTreeView,
    ProjectProgressView,
    TaskProgressView,
    TreeEdgeView,
    TreeNodeView,
    TrendPointView,
    WorkstreamProgressView,
)
from core.application.portfolio_feed_service import PortfolioFeedItemView, PortfolioFeedView
from core.domain.brief import BriefKind, NarrativeBrief
from core.domain.cross_person import CrossPersonRequest, CrossPersonRequestStatus
from core.domain.dead_letter import DeadLetter, DeadLetterStatus
from core.domain.directory import DirectoryUser
from core.domain.escalation import EscalationContact, PodEscalationContacts
from core.domain.graph import EdgeKind, EntityRef, GraphEdge, GraphNode, GraphTree, NodeKind
from core.domain.identity import IdentityLink
from core.domain.risk import DriftFinding, RiskFinding
from core.domain.rollup import Rag, RollupFactor
from core.domain.status import (
    CheckInPreference,
    DeveloperStatus,
    StatusSource,
    WriteBackConsent,
)
from core.domain.sync_status import (
    SYNC_ERROR_MESSAGES,
    SyncErrorKind,
    SyncHealth,
    SyncOutcome,
    SyncSource,
    SyncSourceStatus,
    SyncStatusReport,
    SyncTargetOrigin,
    SyncTargetStatus,
)
from core.domain.writeback import (
    WriteBackAdoption,
    WriteBackAudit,
    WriteBackGate,
    WriteBackGateSource,
    WriteBackStatus,
)
from core.ports.auth import AuthenticatedUser


def _metadata_string(
    metadata: dict[str, str | int | float | bool | None],
    key: str,
) -> str | None:
    value = metadata.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _metadata_list(
    metadata: dict[str, str | int | float | bool | None],
    key: str,
) -> list[str]:
    return _repo_list(metadata.get(key))


def _blank_to_none(value: object) -> object:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def _repo_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = value.replace("\n", ",").split(",")
    elif isinstance(value, list | tuple | set):
        raw_items = [str(item) for item in value]
    else:
        raw_items = [str(value)]
    repos: list[str] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        repo = raw_item.strip()
        if not repo or repo in seen:
            continue
        seen.add(repo)
        repos.append(repo)
    return repos


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    environment: str
    tenant_id: str
    correlation_id: str


class ReadyResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    dependencies: dict[str, bool]
    # Why a dependency is (not) ready, keyed like ``dependencies`` and present
    # only where a probe gave a reason -- e.g. "unauthorized: the endpoint
    # rejected the API key (HTTP 401)" versus "unreachable: ...". Never carries
    # credentials, hosts or response bodies: /ready is unauthenticated.
    details: dict[str, str] = Field(default_factory=dict)


class AuthUserResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    subject: str
    username: str | None = None
    email: str | None = None
    name: str | None = None
    roles: list[str]
    scopes: list[str]

    @classmethod
    def from_user(cls, user: AuthenticatedUser) -> AuthUserResponse:
        return cls(
            subject=user.subject,
            username=user.username,
            email=user.email,
            name=user.name,
            roles=sorted(role.value for role in user.roles),
            scopes=sorted(user.scopes),
        )


class AuthStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    authenticated: bool
    provider: Literal["dev", "oidc_bff"]
    login_url: str | None = None
    message: str | None = None
    user: AuthUserResponse | None = None
    # Which optional local surfaces this tenant serves, so the console shows
    # only what the backend will actually answer. Both default off.
    demo_mode: bool = False
    chat_enabled: bool = False


class DevUserResponse(BaseModel):
    """One switchable person for the local demo persona picker."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    title: str | None = None
    email: str | None = None
    roles: list[str]
    pods: list[str] = Field(default_factory=list)


class DevUsersResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[DevUserResponse]


class LogoutResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    success: bool
    message: str
    redirect_url: str


class GraphNodeDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    kind: NodeKind
    name: str
    metadata: dict[str, str | int | float | bool | None]

    @classmethod
    def from_domain(cls, node: GraphNode) -> GraphNodeDto:
        return cls(id=node.id, kind=node.kind, name=node.name, metadata=dict(node.metadata))


class GraphEdgeDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    from_node_id: str
    to_node_id: str
    kind: EdgeKind
    valid_from: date | None
    valid_to: date | None

    @classmethod
    def from_domain(cls, edge: GraphEdge) -> GraphEdgeDto:
        return cls(
            from_node_id=edge.from_node_id,
            to_node_id=edge.to_node_id,
            kind=edge.kind,
            valid_from=edge.valid_from,
            valid_to=edge.valid_to,
        )


class GraphTreeDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    root: GraphNodeDto
    nodes: list[GraphNodeDto]
    edges: list[GraphEdgeDto]

    @classmethod
    def from_domain(cls, tree: GraphTree) -> GraphTreeDto:
        return cls(
            root=GraphNodeDto.from_domain(tree.root),
            nodes=[GraphNodeDto.from_domain(node) for node in tree.nodes],
            edges=[GraphEdgeDto.from_domain(edge) for edge in tree.edges],
        )


class ConfigNodeResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    kind: NodeKind
    name: str
    description: str | None = None
    code: str | None = None
    jira_project_key: str | None = None
    jira_base_jql: str | None = None
    jira_board_id: str | None = None
    jira_filter_jql: str | None = None
    github_repos: list[str] = Field(default_factory=list)
    metadata: dict[str, str | int | float | bool | None]

    @classmethod
    def from_domain(cls, node: GraphNode) -> ConfigNodeResponse:
        metadata = dict(node.metadata)
        description = metadata.get("description")
        code = metadata.get("code")
        return cls(
            id=node.id,
            kind=node.kind,
            name=node.name,
            description=description if isinstance(description, str) else None,
            code=code if isinstance(code, str) else None,
            jira_project_key=_metadata_string(metadata, "jira_project_key"),
            jira_base_jql=_metadata_string(metadata, "jira_base_jql"),
            jira_board_id=_metadata_string(metadata, "jira_board_id"),
            jira_filter_jql=_metadata_string(metadata, "jira_filter_jql"),
            github_repos=_metadata_list(metadata, "github_repos"),
            metadata=metadata,
        )


class ConfigNodeCreateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None
    code: str | None = None
    jira_project_key: str | None = None
    jira_base_jql: str | None = None
    jira_board_id: str | None = None
    jira_filter_jql: str | None = None
    github_repos: list[str] = Field(default_factory=list)
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_validator(
        "jira_project_key",
        "jira_base_jql",
        "jira_board_id",
        "jira_filter_jql",
        mode="before",
    )
    @classmethod
    def normalize_optional_integration_string(cls, value: object) -> object:
        return _blank_to_none(value)

    @field_validator("github_repos", mode="before")
    @classmethod
    def normalize_github_repos(cls, value: object) -> object:
        return _repo_list(value)


class ConfigNodeUpdateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    code: str | None = None
    jira_project_key: str | None = None
    jira_base_jql: str | None = None
    jira_board_id: str | None = None
    jira_filter_jql: str | None = None
    github_repos: list[str] | None = None
    metadata: dict[str, str | int | float | bool | None] | None = None

    @field_validator(
        "jira_project_key",
        "jira_base_jql",
        "jira_board_id",
        "jira_filter_jql",
        mode="before",
    )
    @classmethod
    def normalize_optional_integration_string(cls, value: object) -> object:
        return _blank_to_none(value)

    @field_validator("github_repos", mode="before")
    @classmethod
    def normalize_github_repos(cls, value: object) -> object:
        if value is None:
            return None
        return _repo_list(value)


class ConfigEdgeResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    from_node_id: str
    to_node_id: str
    kind: EdgeKind
    valid_from: date | None
    valid_to: date | None
    metadata: dict[str, str | int | float | bool | None]

    @classmethod
    def from_domain(cls, edge: GraphEdge) -> ConfigEdgeResponse:
        return cls(
            from_node_id=edge.from_node_id,
            to_node_id=edge.to_node_id,
            kind=edge.kind,
            valid_from=edge.valid_from,
            valid_to=edge.valid_to,
            metadata=dict(edge.metadata),
        )


class ProgramProjectLinkRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    program_id: str = Field(min_length=1)


class PodMemberLinkRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: str = Field(min_length=1)


class MemberTaskAssignmentRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str = Field(min_length=1)


class WorkItemCreateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    state: str = Field(default="proposed", min_length=1)
    item_type: str = Field(default="feature", min_length=1)
    repo: str | None = None
    branch: str | None = None
    pr_id: str | None = None
    workstream_id: str | None = None
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class WorkItemFromBranchRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    repo: str = Field(min_length=1)
    branch: str = Field(min_length=1)
    name: str | None = None
    item_type: str = Field(default="feature", min_length=1)
    workstream_id: str | None = None
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class WorkItemFromPrRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    repo: str = Field(min_length=1)
    pr_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    item_type: str = Field(default="feature", min_length=1)
    workstream_id: str | None = None
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class WorkItemTransitionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    new_state: str = Field(min_length=1)


class DirectoryPersonResponse(BaseModel):
    """A person named in a node's metadata (``key``, e.g. ``owner_id``).

    ``member_id`` and ``name`` are null when ``id`` matches no member.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    id: str
    member_id: str | None
    name: str | None

    @classmethod
    def from_view(cls, view: DirectoryPersonView) -> DirectoryPersonResponse:
        return cls(key=view.key, id=view.id, member_id=view.member_id, name=view.name)


class DirectoryItemResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    kind: NodeKind
    name: str
    description: str | None
    code: str | None
    metadata: dict[str, str | int | float | bool | None]
    rag: Rag | None
    source: StatusSource | None
    program_ids: list[str]
    project_ids: list[str]
    workstream_ids: list[str]
    pod_ids: list[str]
    member_ids: list[str]
    task_ids: list[str]
    people: list[DirectoryPersonResponse]

    @classmethod
    def from_view(cls, view: DirectoryItemView) -> DirectoryItemResponse:
        return cls(
            id=view.id,
            kind=view.kind,
            name=view.name,
            description=view.description,
            code=view.code,
            metadata=dict(view.metadata),
            rag=view.rag,
            source=view.source,
            program_ids=list(view.program_ids),
            project_ids=list(view.project_ids),
            workstream_ids=list(view.workstream_ids),
            pod_ids=list(view.pod_ids),
            member_ids=list(view.member_ids),
            task_ids=list(view.task_ids),
            people=[DirectoryPersonResponse.from_view(person) for person in view.people],
        )


class DirectoryUserResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    external_id: str
    display_name: str
    email: str | None
    handle: str | None
    avatar_url: str | None
    title: str | None
    is_active: bool
    source: str
    metadata: dict[str, str | int | float | bool | None]

    @classmethod
    def from_domain(cls, user: DirectoryUser) -> DirectoryUserResponse:
        return cls(
            external_id=user.external_id,
            display_name=user.display_name,
            email=user.email,
            handle=user.handle,
            avatar_url=user.avatar_url,
            title=user.title,
            is_active=user.is_active,
            source=user.source,
            metadata=dict(user.metadata),
        )


class DirectorySearchResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[DirectoryUserResponse]
    total: int


class DirectorySyncResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    synced_count: int
    deactivated_count: int


class MemberFromDirectoryRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    external_ids: list[str] = Field(min_length=1)


class ChatWebhookResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    message_id: str


class ChatSimulatorStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool
    tenant_id: str
    provider: str
    message_count: int


class ChatSimulatorMessageResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    message_id: str
    channel_id: str
    user_id: str
    direction: str
    text: str
    created_at: datetime
    correlation_id: str | None = None
    purpose: str | None = None
    reply_to_message_id: str | None = None
    metadata: dict[str, str | int | float | bool | None]


class ChatSimulatorMessagesResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[ChatSimulatorMessageResponse]


class ChatSimulatorReplyRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1)
    received_at: datetime | None = None


class ChatSimulatorReplyResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    message_id: str
    status: str
    processed_message_id: str


class ChatSimulatorUserMessageRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1)
    received_at: datetime | None = None
    # Defaults to the chat user id, which is how the demo tenant is wired.
    developer_id: str | None = None
    developer_name: str | None = None


class ChatSimulatorUserMessageResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    message_id: str
    status: str
    processed_message_id: str
    started_checkin: bool = False


class EntityRefDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    kind: NodeKind
    id: str

    @classmethod
    def from_domain(cls, ref: EntityRef) -> EntityRefDto:
        return cls(tenant_id=ref.tenant_id, kind=ref.kind, id=ref.id)

    @classmethod
    def from_optional(cls, ref: EntityRef | None) -> EntityRefDto | None:
        return cls.from_domain(ref) if ref is not None else None


class RollupFactorDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    description: str
    contributes: Rag
    source_ref: EntityRefDto
    kind: str
    blocker_id: str | None
    work_item_ref: EntityRefDto | None
    unattributed: bool
    applies_to_pod_ids: list[str]

    @classmethod
    def from_domain(cls, factor: RollupFactor) -> RollupFactorDto:
        return cls(
            description=factor.description,
            contributes=factor.contributes,
            source_ref=EntityRefDto(
                tenant_id=factor.source_ref.tenant_id,
                kind=factor.source_ref.kind,
                id=factor.source_ref.id,
            ),
            kind=factor.kind.value,
            blocker_id=factor.blocker_id,
            work_item_ref=EntityRefDto.from_optional(factor.work_item_ref),
            unattributed=factor.unattributed,
            applies_to_pod_ids=list(factor.applies_to_pod_ids),
        )


class FocusTaskDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    rag: Rag
    source: StatusSource
    confidence: float | None
    deadline: date | None

    @classmethod
    def from_view(cls, task: FocusTaskView) -> FocusTaskDto:
        return cls(
            id=task.id,
            name=task.name,
            rag=task.rag,
            source=task.source,
            confidence=task.confidence,
            deadline=task.deadline,
        )


class FocusItemDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["task", "blocker"]
    label: str
    source: StatusSource
    source_ref: EntityRefDto
    confidence: float | None
    deadline: date | None

    @classmethod
    def from_view(cls, item: FocusItemView) -> FocusItemDto:
        return cls(
            kind=item.kind,
            label=item.label,
            source=item.source,
            source_ref=EntityRefDto(
                tenant_id=item.source_ref.tenant_id,
                kind=item.source_ref.kind,
                id=item.source_ref.id,
            ),
            confidence=item.confidence,
            deadline=item.deadline,
        )


class BlockerDetailDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    blocker_id: str
    description: str
    work_item_id: str | None
    work_item_name: str | None
    pod_id: str | None
    unattributed: bool
    first_seen_on: date
    age_days: int

    @classmethod
    def from_view(cls, detail: BlockerDetailView) -> BlockerDetailDto:
        return cls(
            blocker_id=detail.blocker_id,
            description=detail.description,
            work_item_id=detail.work_item_id,
            work_item_name=detail.work_item_name,
            pod_id=detail.pod_id,
            unattributed=detail.unattributed,
            first_seen_on=detail.first_seen_on,
            age_days=detail.age_days,
        )


class FocusResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    developer_id: str
    developer_name: str
    as_of: date
    status_source: StatusSource
    developer_confirmed: bool
    status_as_of: date | None
    summary: str
    blockers: list[str]
    blocker_details: list[BlockerDetailDto]
    tasks: list[FocusTaskDto]
    focus: list[FocusItemDto]

    @classmethod
    def from_view(cls, view: FocusView) -> FocusResponse:
        return cls(
            developer_id=view.developer_id,
            developer_name=view.developer_name,
            as_of=view.as_of,
            status_source=view.status_source,
            developer_confirmed=view.developer_confirmed,
            status_as_of=view.status_as_of,
            summary=view.summary,
            blockers=list(view.blockers),
            blocker_details=[BlockerDetailDto.from_view(detail) for detail in view.blocker_details],
            tasks=[FocusTaskDto.from_view(task) for task in view.tasks],
            focus=[FocusItemDto.from_view(item) for item in view.focus],
        )


class BlockerDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    blocker_id: str
    description: str
    age_days: int
    owner_id: str
    owner_name: str
    source: StatusSource
    status_as_of: date
    source_ref: EntityRefDto
    work_item_ref: EntityRefDto | None
    pod_ref: EntityRefDto | None
    unattributed: bool
    first_seen_on: date

    @classmethod
    def from_view(cls, blocker: BlockerView) -> BlockerDto:
        return cls(
            id=blocker.id,
            blocker_id=blocker.blocker_id,
            description=blocker.description,
            age_days=blocker.age_days,
            owner_id=blocker.owner_id,
            owner_name=blocker.owner_name,
            source=blocker.source,
            status_as_of=blocker.status_as_of,
            source_ref=EntityRefDto(
                tenant_id=blocker.source_ref.tenant_id,
                kind=blocker.source_ref.kind,
                id=blocker.source_ref.id,
            ),
            work_item_ref=EntityRefDto.from_optional(blocker.work_item_ref),
            pod_ref=EntityRefDto.from_optional(blocker.pod_ref),
            unattributed=blocker.unattributed,
            first_seen_on=blocker.first_seen_on,
        )


class PodBlockersResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    pod_id: str
    pod_name: str
    as_of: date
    blockers: list[BlockerDto]

    @classmethod
    def from_view(cls, view: PodBlockersView) -> PodBlockersResponse:
        return cls(
            pod_id=view.pod_id,
            pod_name=view.pod_name,
            as_of=view.as_of,
            blockers=[BlockerDto.from_view(blocker) for blocker in view.blockers],
        )


class CheckinDeveloperDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    developer_id: str
    developer_name: str
    state: Literal["confirmed", "partial", "stale", "missing"]
    source: StatusSource
    status_as_of: date | None
    summary: str

    @classmethod
    def from_view(cls, developer: CheckinDeveloperView) -> CheckinDeveloperDto:
        return cls(
            developer_id=developer.developer_id,
            developer_name=developer.developer_name,
            state=developer.state,
            source=developer.source,
            status_as_of=developer.status_as_of,
            summary=developer.summary,
        )


class PodCheckinsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    pod_id: str
    pod_name: str
    as_of: date
    confirmed: int
    partial: int
    stale: int
    missing: int
    developers: list[CheckinDeveloperDto]

    @classmethod
    def from_view(cls, view: PodCheckinsView) -> PodCheckinsResponse:
        return cls(
            pod_id=view.pod_id,
            pod_name=view.pod_name,
            as_of=view.as_of,
            confirmed=view.confirmed,
            partial=view.partial,
            stale=view.stale,
            missing=view.missing,
            developers=[CheckinDeveloperDto.from_view(item) for item in view.developers],
        )


class PodRollupResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    pod_id: str
    pod_name: str
    as_of: date
    rag: Rag
    source: StatusSource
    factors: list[RollupFactorDto]
    # Names of the nodes the factors cite, keyed by node id. A cited node
    # missing here is shown by its id, never guessed.
    source_names: dict[str, str]

    @classmethod
    def from_view(cls, view: PodRollupView) -> PodRollupResponse:
        return cls(
            pod_id=view.pod_id,
            pod_name=view.pod_name,
            as_of=view.as_of,
            rag=view.rag,
            source=view.source,
            factors=[RollupFactorDto.from_domain(factor) for factor in view.factors],
            source_names=dict(view.source_names),
        )


class PodTaskOwnerDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str

    @classmethod
    def from_view(cls, owner: PodTaskOwnerView) -> PodTaskOwnerDto:
        return cls(id=owner.id, name=owner.name)


class PodTaskBlockerDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    blocker_id: str
    description: str
    first_seen_on: date
    age_days: int

    @classmethod
    def from_view(cls, blocker: PodTaskBlockerView) -> PodTaskBlockerDto:
        return cls(
            blocker_id=blocker.blocker_id,
            description=blocker.description,
            first_seen_on=blocker.first_seen_on,
            age_days=blocker.age_days,
        )


class PodTaskDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    rag: Rag
    source: StatusSource
    confidence: float | None
    deadline: date | None
    owners: list[PodTaskOwnerDto]
    blocked: bool
    open_blockers: list[PodTaskBlockerDto]

    @classmethod
    def from_view(cls, task: PodTaskView) -> PodTaskDto:
        return cls(
            id=task.id,
            name=task.name,
            rag=task.rag,
            source=task.source,
            confidence=task.confidence,
            deadline=task.deadline,
            owners=[PodTaskOwnerDto.from_view(owner) for owner in task.owners],
            blocked=task.blocked,
            open_blockers=[PodTaskBlockerDto.from_view(item) for item in task.open_blockers],
        )


class PodTasksResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    pod_id: str
    pod_name: str
    as_of: date
    tasks: list[PodTaskDto]

    @classmethod
    def from_view(cls, view: PodTasksView) -> PodTasksResponse:
        return cls(
            pod_id=view.pod_id,
            pod_name=view.pod_name,
            as_of=view.as_of,
            tasks=[PodTaskDto.from_view(task) for task in view.tasks],
        )


class TaskProgressDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    rag: Rag
    source: StatusSource
    confidence: float | None
    deadline: date | None

    @classmethod
    def from_view(cls, task: TaskProgressView) -> TaskProgressDto:
        return cls(
            id=task.id,
            name=task.name,
            rag=task.rag,
            source=task.source,
            confidence=task.confidence,
            deadline=task.deadline,
        )


class ProjectProgressResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str
    project_name: str
    as_of: date
    rag: Rag
    source: StatusSource
    confidence: float | None
    percent_complete: float
    total_tasks: int
    green_tasks: int
    amber_tasks: int
    red_tasks: int
    unknown_tasks: int
    factors: list[RollupFactorDto]
    # Names of the nodes the factors cite, keyed by node id. A cited node
    # missing here is shown by its id, never guessed.
    source_names: dict[str, str]
    tasks: list[TaskProgressDto]

    @classmethod
    def from_view(cls, view: ProjectProgressView) -> ProjectProgressResponse:
        return cls(
            project_id=view.project_id,
            project_name=view.project_name,
            as_of=view.as_of,
            rag=view.rag,
            source=view.source,
            confidence=view.confidence,
            percent_complete=view.percent_complete,
            total_tasks=view.total_tasks,
            green_tasks=view.green_tasks,
            amber_tasks=view.amber_tasks,
            red_tasks=view.red_tasks,
            unknown_tasks=view.unknown_tasks,
            factors=[RollupFactorDto.from_domain(factor) for factor in view.factors],
            source_names=dict(view.source_names),
            tasks=[TaskProgressDto.from_view(task) for task in view.tasks],
        )


class WorkstreamProgressResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    workstream_id: str
    workstream_name: str
    as_of: date
    rag: Rag
    source: StatusSource
    confidence: float | None
    percent_complete: float
    total_tasks: int
    green_tasks: int
    amber_tasks: int
    red_tasks: int
    unknown_tasks: int
    factors: list[RollupFactorDto]
    # Names of the nodes the factors cite, keyed by node id. A cited node
    # missing here is shown by its id, never guessed.
    source_names: dict[str, str]
    tasks: list[TaskProgressDto]

    @classmethod
    def from_view(cls, view: WorkstreamProgressView) -> WorkstreamProgressResponse:
        return cls(
            workstream_id=view.workstream_id,
            workstream_name=view.workstream_name,
            as_of=view.as_of,
            rag=view.rag,
            source=view.source,
            confidence=view.confidence,
            percent_complete=view.percent_complete,
            total_tasks=view.total_tasks,
            green_tasks=view.green_tasks,
            amber_tasks=view.amber_tasks,
            red_tasks=view.red_tasks,
            unknown_tasks=view.unknown_tasks,
            factors=[RollupFactorDto.from_domain(factor) for factor in view.factors],
            source_names=dict(view.source_names),
            tasks=[TaskProgressDto.from_view(task) for task in view.tasks],
        )


class PersonaTreeNodeDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    kind: NodeKind
    name: str
    rag: Rag | None
    source: StatusSource | None
    confidence: float | None
    factors: list[RollupFactorDto]

    @classmethod
    def from_view(cls, node: TreeNodeView) -> PersonaTreeNodeDto:
        return cls(
            id=node.id,
            kind=node.kind,
            name=node.name,
            rag=node.rag,
            source=node.source,
            confidence=node.confidence,
            factors=[RollupFactorDto.from_domain(factor) for factor in node.factors],
        )


class PersonaTreeEdgeDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    from_node_id: str
    to_node_id: str
    kind: EdgeKind

    @classmethod
    def from_view(cls, edge: TreeEdgeView) -> PersonaTreeEdgeDto:
        return cls(
            from_node_id=edge.from_node_id,
            to_node_id=edge.to_node_id,
            kind=edge.kind,
        )


class ProgramTreeResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    root_id: str
    as_of: date
    nodes: list[PersonaTreeNodeDto]
    edges: list[PersonaTreeEdgeDto]

    @classmethod
    def from_view(cls, view: ProgramTreeView) -> ProgramTreeResponse:
        return cls(
            root_id=view.root_id,
            as_of=view.as_of,
            nodes=[PersonaTreeNodeDto.from_view(node) for node in view.nodes],
            edges=[PersonaTreeEdgeDto.from_view(edge) for edge in view.edges],
        )


class HeatmapCellDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    row: str
    column: str
    entity_ref: EntityRefDto
    rag: Rag
    source: StatusSource
    why: str
    source_ref: EntityRefDto

    @classmethod
    def from_view(cls, cell: HeatmapCellView) -> HeatmapCellDto:
        return cls(
            row=cell.row,
            column=cell.column,
            entity_ref=EntityRefDto(
                tenant_id=cell.entity_ref.tenant_id,
                kind=cell.entity_ref.kind,
                id=cell.entity_ref.id,
            ),
            rag=cell.rag,
            source=cell.source,
            why=cell.why,
            source_ref=EntityRefDto(
                tenant_id=cell.source_ref.tenant_id,
                kind=cell.source_ref.kind,
                id=cell.source_ref.id,
            ),
        )


class PortfolioHeatmapResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    as_of: date
    rows: list[str]
    columns: list[str]
    cells: list[HeatmapCellDto]

    @classmethod
    def from_view(cls, view: PortfolioHeatmapView) -> PortfolioHeatmapResponse:
        return cls(
            as_of=view.as_of,
            rows=list(view.rows),
            columns=list(view.columns),
            cells=[HeatmapCellDto.from_view(cell) for cell in view.cells],
        )


class TrendPointDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    as_of: date
    rag: Rag
    source: StatusSource
    score: int

    @classmethod
    def from_view(cls, point: TrendPointView) -> TrendPointDto:
        return cls(
            as_of=point.as_of,
            rag=point.rag,
            source=point.source,
            score=point.score,
        )


class NodeTrendResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    entity_ref: EntityRefDto
    window_days: int
    start: date
    end: date
    points: list[TrendPointDto]

    @classmethod
    def from_view(cls, view: NodeTrendView) -> NodeTrendResponse:
        return cls(
            entity_ref=EntityRefDto(
                tenant_id=view.entity_ref.tenant_id,
                kind=view.entity_ref.kind,
                id=view.entity_ref.id,
            ),
            window_days=view.window_days,
            start=view.start,
            end=view.end,
            points=[TrendPointDto.from_view(point) for point in view.points],
        )


class WorkItemFlowDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    state: str
    item_type: str
    repo: str | None
    branch: str | None
    pr_id: str | None
    workstream_ids: list[str]
    age_days: int | None
    cycle_time_days: float | None
    last_transition_at: datetime | None

    @classmethod
    def from_view(cls, view: WorkItemFlowView) -> WorkItemFlowDto:
        return cls(
            id=view.id,
            name=view.name,
            state=view.state,
            item_type=view.item_type,
            repo=view.repo,
            branch=view.branch,
            pr_id=view.pr_id,
            workstream_ids=list(view.workstream_ids),
            age_days=view.age_days,
            cycle_time_days=view.cycle_time_days,
            last_transition_at=view.last_transition_at,
        )


class WorkstreamFlowSummaryDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    workstream_id: str
    workstream_name: str
    active_count: int
    features_in_flight: int
    completed_count: int
    stale_count: int
    abandoned_count: int
    avg_cycle_time_days: float | None
    avg_pr_age_days: float | None

    @classmethod
    def from_view(cls, view: WorkstreamFlowSummaryView) -> WorkstreamFlowSummaryDto:
        return cls(
            workstream_id=view.workstream_id,
            workstream_name=view.workstream_name,
            active_count=view.active_count,
            features_in_flight=view.features_in_flight,
            completed_count=view.completed_count,
            stale_count=view.stale_count,
            abandoned_count=view.abandoned_count,
            avg_cycle_time_days=view.avg_cycle_time_days,
            avg_pr_age_days=view.avg_pr_age_days,
        )


class WorkstreamFlowResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

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
    work_items: list[WorkItemFlowDto]

    @classmethod
    def from_view(cls, view: WorkstreamFlowView) -> WorkstreamFlowResponse:
        return cls(
            workstream_id=view.workstream_id,
            workstream_name=view.workstream_name,
            as_of=view.as_of,
            active_count=view.active_count,
            features_in_flight=view.features_in_flight,
            completed_count=view.completed_count,
            stale_count=view.stale_count,
            abandoned_count=view.abandoned_count,
            avg_cycle_time_days=view.avg_cycle_time_days,
            avg_pr_age_days=view.avg_pr_age_days,
            work_items=[WorkItemFlowDto.from_view(item) for item in view.work_items],
        )


class PortfolioFlowResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    as_of: date
    active_count: int
    features_in_flight: int
    completed_count: int
    stale_count: int
    abandoned_count: int
    avg_cycle_time_days: float | None
    avg_pr_age_days: float | None
    workstreams: list[WorkstreamFlowSummaryDto]

    @classmethod
    def from_view(cls, view: PortfolioFlowView) -> PortfolioFlowResponse:
        return cls(
            as_of=view.as_of,
            active_count=view.active_count,
            features_in_flight=view.features_in_flight,
            completed_count=view.completed_count,
            stale_count=view.stale_count,
            abandoned_count=view.abandoned_count,
            avg_cycle_time_days=view.avg_cycle_time_days,
            avg_pr_age_days=view.avg_pr_age_days,
            workstreams=[WorkstreamFlowSummaryDto.from_view(item) for item in view.workstreams],
        )


class PortfolioFeedItemResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    kind: str
    summary: str
    entity_ref: EntityRefDto
    observed_at: datetime
    details: dict[str, str | int | float | bool | None]

    @classmethod
    def from_view(cls, view: PortfolioFeedItemView) -> PortfolioFeedItemResponse:
        return cls(
            source=view.source,
            kind=view.kind,
            summary=view.summary,
            entity_ref=EntityRefDto(
                tenant_id=view.entity_ref.tenant_id,
                kind=view.entity_ref.kind,
                id=view.entity_ref.id,
            ),
            observed_at=view.observed_at,
            details=dict(view.details),
        )


class PortfolioFeedResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    as_of: datetime
    since: datetime
    items: list[PortfolioFeedItemResponse]

    @classmethod
    def from_view(cls, view: PortfolioFeedView) -> PortfolioFeedResponse:
        return cls(
            as_of=view.as_of,
            since=view.since,
            items=[PortfolioFeedItemResponse.from_view(item) for item in view.items],
        )


class NarrativeBriefResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: BriefKind
    scope_id: str
    title: str
    body: str
    generated_at: datetime
    sources: list[str]

    @classmethod
    def from_domain(cls, brief: NarrativeBrief) -> NarrativeBriefResponse:
        return cls(
            kind=brief.kind,
            scope_id=brief.scope_id,
            title=brief.title,
            body=brief.body,
            generated_at=brief.generated_at,
            sources=list(brief.sources),
        )


class NarrativeBriefsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    briefs: list[NarrativeBriefResponse]


class RiskEvidenceDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    identifier: str
    url: str | None
    url_is_user_supplied: bool


class RiskFindingResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule_id: str
    severity: Rag
    entity_ref: EntityRefDto
    workstream_id: str | None
    reason: str
    evidence: RiskEvidenceDto
    age_days: int
    detected_at: datetime
    status: str
    owner_id: str | None
    owner_status_summary: str | None
    owner_status_source: StatusSource | None
    owner_status_as_of: date | None
    owner_status_has_blockers: bool = Field(
        description=(
            "True when the owner has an open blocker relevant to this finding's "
            "work item: either attributed to that work item or unattributed "
            "(could concern anything). A blocker attributed to a different work "
            "item does not count."
        ),
    )
    is_watermelon: bool

    @classmethod
    def from_domain(cls, finding: RiskFinding) -> RiskFindingResponse:
        return cls(
            rule_id=finding.rule_id.value,
            severity=finding.severity,
            entity_ref=EntityRefDto(
                tenant_id=finding.entity_ref.tenant_id,
                kind=finding.entity_ref.kind,
                id=finding.entity_ref.id,
            ),
            workstream_id=finding.workstream_id,
            reason=finding.reason,
            evidence=RiskEvidenceDto(
                identifier=finding.evidence.identifier,
                url=finding.evidence.url,
                url_is_user_supplied=finding.evidence.url_is_user_supplied,
            ),
            age_days=finding.age_days,
            detected_at=finding.detected_at,
            status=finding.status.value,
            owner_id=finding.owner_id,
            owner_status_summary=finding.owner_status_summary,
            owner_status_source=finding.owner_status_source,
            owner_status_as_of=finding.owner_status_as_of,
            owner_status_has_blockers=finding.owner_status_has_blockers,
            is_watermelon=finding.is_watermelon,
        )


class DriftFindingResponse(BaseModel):
    """A continuous drift ("watermelon") finding for persona risk views.

    Carries only derived, sanitised fields -- never raw DM/reply content -- so
    the stated-vs-actual divergence is explicit alongside signal-only risks.
    """

    model_config = ConfigDict(frozen=True)

    kind: str
    severity: Rag
    entity_ref: EntityRefDto
    workstream_id: str | None
    reason: str
    detected_at: datetime
    owner_id: str | None
    stated_source: StatusSource | None
    evidence: RiskEvidenceDto | None
    child_entity_ref: EntityRefDto | None

    @classmethod
    def from_domain(cls, finding: DriftFinding) -> DriftFindingResponse:
        return cls(
            kind=finding.kind.value,
            severity=finding.severity,
            entity_ref=EntityRefDto(
                tenant_id=finding.entity_ref.tenant_id,
                kind=finding.entity_ref.kind,
                id=finding.entity_ref.id,
            ),
            workstream_id=finding.workstream_id,
            reason=finding.reason,
            detected_at=finding.detected_at,
            owner_id=finding.owner_id,
            stated_source=finding.stated_source,
            evidence=(
                RiskEvidenceDto(
                    identifier=finding.evidence.identifier,
                    url=finding.evidence.url,
                    url_is_user_supplied=finding.evidence.url_is_user_supplied,
                )
                if finding.evidence is not None
                else None
            ),
            child_entity_ref=(
                EntityRefDto(
                    tenant_id=finding.child_entity_ref.tenant_id,
                    kind=finding.child_entity_ref.kind,
                    id=finding.child_entity_ref.id,
                )
                if finding.child_entity_ref is not None
                else None
            ),
        )


class CrossPersonRequestResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    requester_id: str
    counterpart_id: str | None
    counterpart_display_name: str | None
    counterpart_email: str | None
    kind: str
    status: CrossPersonRequestStatus
    note: str
    raw_name: str | None
    source_correlation_id: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, request: CrossPersonRequest) -> CrossPersonRequestResponse:
        return cls(
            id=request.id,
            requester_id=request.requester_id,
            counterpart_id=request.counterpart_id,
            counterpart_display_name=request.counterpart_display_name,
            counterpart_email=request.counterpart_email,
            kind=request.kind.value,
            status=request.status,
            note=request.note,
            raw_name=request.raw_name,
            source_correlation_id=request.source_correlation_id,
            created_at=request.created_at,
            updated_at=request.updated_at,
        )


class CrossPersonRequestsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    requests: list[CrossPersonRequestResponse]


class CrossPersonRequestStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: CrossPersonRequestStatus


class ProjectRisksResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str
    as_of: date
    risks: list[RiskFindingResponse]
    drift: list[DriftFindingResponse] = []


class PortfolioRisksResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    as_of: date
    risks: list[RiskFindingResponse]
    drift: list[DriftFindingResponse] = []


class AskResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    answer: str
    references: list[str]
    tools_used: list[str]
    trace_id: str

    @classmethod
    def from_view(cls, view: AskResponseView) -> AskResponse:
        return cls(
            answer=view.answer,
            references=list(view.references),
            tools_used=list(view.tools_used),
            trace_id=view.trace_id,
        )


class AskRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    question: str = Field(min_length=1)
    as_of: date | None = None


class WorkflowDispatchResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    workflow_id: str


class DeadLetterResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    kind: str
    conversation_key: str
    event_ids: list[str]
    reason: str
    attempts: int
    first_seen_at: datetime
    dead_lettered_at: datetime
    status: DeadLetterStatus
    rearmed_at: datetime | None = None

    @classmethod
    def from_domain(cls, dead_letter: DeadLetter) -> DeadLetterResponse:
        return cls(
            id=dead_letter.id,
            tenant_id=dead_letter.tenant_id,
            kind=dead_letter.kind,
            conversation_key=dead_letter.conversation_key,
            event_ids=list(dead_letter.event_ids),
            reason=dead_letter.reason,
            attempts=dead_letter.attempts,
            first_seen_at=dead_letter.first_seen_at,
            dead_lettered_at=dead_letter.dead_lettered_at,
            status=dead_letter.status,
            rearmed_at=dead_letter.rearmed_at,
        )


class DeadLettersResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    dead_letters: list[DeadLetterResponse]


def _sync_error_message(kind: SyncErrorKind | None) -> str | None:
    # Only the fixed message for the recorded category ever leaves the API; no
    # exception or provider text is stored, so none can be returned.
    return SYNC_ERROR_MESSAGES[kind] if kind is not None else None


class SyncTargetStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    scope: str
    label: str
    detail: str | None = None
    configured: bool
    health: SyncHealth
    last_synced_at: datetime | None = None
    last_attempt_at: datetime | None = None
    last_outcome: SyncOutcome | None = None
    last_error: str | None = None
    items_synced: int | None = None

    @classmethod
    def from_domain(cls, target: SyncTargetStatus) -> SyncTargetStatusResponse:
        return cls(
            scope=target.scope,
            label=target.label,
            detail=target.detail,
            configured=target.configured,
            health=target.health,
            last_synced_at=target.last_synced_at,
            last_attempt_at=target.last_attempt_at,
            last_outcome=target.last_outcome,
            last_error=_sync_error_message(target.last_error),
            items_synced=target.items_synced,
        )


class SyncSourceStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: SyncSource
    provider: str
    simulated: bool
    sync_enabled: bool
    schedule: str | None = None
    stale_after_minutes: int | None = None
    target_origin: SyncTargetOrigin
    health: SyncHealth
    last_synced_at: datetime | None = None
    last_attempt_at: datetime | None = None
    last_error: str | None = None
    newest_item_at: datetime | None = None
    config_error: str | None = None
    provider_error: str | None = None
    targets: list[SyncTargetStatusResponse]

    @classmethod
    def from_domain(cls, source: SyncSourceStatus) -> SyncSourceStatusResponse:
        return cls(
            source=source.source,
            provider=source.provider,
            simulated=source.simulated,
            sync_enabled=source.sync_enabled,
            schedule=source.schedule,
            stale_after_minutes=source.stale_after_minutes,
            target_origin=source.target_origin,
            health=source.health,
            last_synced_at=source.last_synced_at,
            last_attempt_at=source.last_attempt_at,
            last_error=_sync_error_message(source.last_error),
            newest_item_at=source.newest_item_at,
            config_error=source.config_error,
            provider_error=source.provider_error,
            targets=[SyncTargetStatusResponse.from_domain(target) for target in source.targets],
        )


class SyncStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    generated_at: datetime
    sources: list[SyncSourceStatusResponse]

    @classmethod
    def from_domain(cls, report: SyncStatusReport) -> SyncStatusResponse:
        return cls(
            generated_at=report.generated_at,
            sources=[SyncSourceStatusResponse.from_domain(source) for source in report.sources],
        )


class WriteBackRevertResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    audit_id: str
    issue_key: str
    from_state: str | None
    to_state: str | None
    status: WriteBackStatus

    @classmethod
    def from_domain(cls, audit: WriteBackAudit) -> WriteBackRevertResponse:
        return cls(
            audit_id=audit.id,
            issue_key=audit.issue_key,
            from_state=audit.before_state,
            to_state=audit.after_state,
            status=audit.status,
        )


class WriteBackAdoptionEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    issue_key: str
    to_state: str
    applied_at: datetime
    correlation_id: str

    @classmethod
    def from_domain(cls, audit: WriteBackAudit) -> WriteBackAdoptionEntry:
        # Identifier-only: never expose the developer note or any DM/reply text.
        return cls(
            issue_key=audit.issue_key,
            to_state=audit.after_state or audit.target_state,
            applied_at=audit.created_at,
            correlation_id=audit.correlation_id,
        )


class WriteBackAdoptionResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    applied_count: int
    recent: list[WriteBackAdoptionEntry]

    @classmethod
    def from_domain(cls, adoption: WriteBackAdoption) -> WriteBackAdoptionResponse:
        return cls(
            applied_count=adoption.applied_count,
            recent=[WriteBackAdoptionEntry.from_domain(entry) for entry in adoption.recent],
        )


class CheckinDispatchRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    developer_id: str
    developer_name: str | None = None
    chat_external_id: str | None = None
    checkin_date: date | None = None


class JiraSyncDispatchRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    project_key: str
    container_id: str | None = None
    observed_at: datetime | None = None


class GithubSyncDispatchRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    repo_name: str
    observed_at: datetime | None = None


class CalendarSyncDispatchRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    user_id: str
    start: date
    end: date
    display_name: str | None = None
    observed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_window(self) -> CalendarSyncDispatchRequest:
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class CheckinPreferenceResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    developer_id: str
    local_time: time
    timezone: str | None
    weekdays: list[int]
    reply_wait_seconds: int
    final_reply_wait_seconds: int

    @classmethod
    def from_domain(cls, preference: CheckInPreference) -> CheckinPreferenceResponse:
        return cls(
            developer_id=preference.developer_id,
            local_time=preference.local_time,
            timezone=preference.timezone,
            weekdays=list(preference.weekdays),
            reply_wait_seconds=preference.reply_wait_seconds,
            final_reply_wait_seconds=preference.final_reply_wait_seconds,
        )


# Reply windows are stored as a Postgres INTEGER of seconds. A larger value
# can't be saved, so it is a 422 here instead of a database error.
MAX_CHECKIN_WAIT_SECONDS = 2_147_483_647


class CheckinPreferenceUpdateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    local_time: time | None = None
    timezone: str | None = None
    weekdays: list[int] | None = None
    reply_wait_seconds: int | None = Field(default=None, ge=0, le=MAX_CHECKIN_WAIT_SECONDS)
    final_reply_wait_seconds: int | None = Field(default=None, ge=0, le=MAX_CHECKIN_WAIT_SECONDS)

    @field_validator("weekdays")
    @classmethod
    def validate_weekdays(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return value
        if any(day < 0 or day > 6 for day in value):
            raise ValueError("weekdays must be in the range 0..6")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value


class IdentityLinkResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    developer_id: str
    chat_user_id: str | None
    jira_account_id: str | None
    jira_email: str | None
    vcs_username: str | None

    @classmethod
    def from_domain(cls, link: IdentityLink) -> IdentityLinkResponse:
        return cls(
            developer_id=link.developer_id,
            chat_user_id=link.chat_user_id,
            jira_account_id=link.jira_account_id,
            jira_email=link.jira_email,
            vcs_username=link.vcs_username,
        )


class IdentityLinkUpdateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    chat_user_id: str | None = None
    jira_account_id: str | None = None
    jira_email: str | None = None
    vcs_username: str | None = None


class IdentityAutoMatchMemberDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    filled: list[str]


class IdentityAutoMatchResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    updated_count: int
    members: list[IdentityAutoMatchMemberDto]

    @classmethod
    def from_domain(cls, result: IdentityAutoMatchResult) -> IdentityAutoMatchResponse:
        return cls(
            updated_count=result.updated_count,
            members=[
                IdentityAutoMatchMemberDto(
                    id=member.id, name=member.name, filled=list(member.filled)
                )
                for member in result.members
            ],
        )


class UnmappedMemberResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    missing: list[str]

    @classmethod
    def from_domain(cls, member: UnmappedMember) -> UnmappedMemberResponse:
        return cls(id=member.id, name=member.name, missing=list(member.missing))


class EscalationContactDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    chat_external_id: str = Field(min_length=1)
    display_name: str | None = None
    # The member this contact reaches; None when its chat id belongs to no member.
    member_id: str | None = None


class EscalationContactUpdateDto(BaseModel):
    """Pick a member. A bare chat id is kept only if it is a member's or already stored."""

    model_config = ConfigDict(frozen=True)

    member_id: str | None = Field(default=None, min_length=1)
    chat_external_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def require_member_or_chat_id(self) -> EscalationContactUpdateDto:
        if self.member_id is None and self.chat_external_id is None:
            raise ValueError("member_id or chat_external_id is required")
        return self


class EscalationCandidateResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    member_id: str
    name: str
    # None when the member has no chat id linked, so cannot be messaged.
    chat_user_id: str | None
    in_pod: bool
    pod_role: str | None

    @classmethod
    def from_domain(cls, candidate: EscalationCandidate) -> EscalationCandidateResponse:
        return cls(
            member_id=candidate.member_id,
            name=candidate.name,
            chat_user_id=candidate.chat_user_id,
            in_pod=candidate.in_pod,
            pod_role=candidate.pod_role,
        )


class PodEscalationContactsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    pod_id: str
    scrum_master: EscalationContactDto | None
    manager: EscalationContactDto | None

    @classmethod
    def from_domain(
        cls, pod_id: str, contacts: PodEscalationContacts
    ) -> PodEscalationContactsResponse:
        return cls(
            pod_id=pod_id,
            scrum_master=_escalation_contact_dto(contacts.scrum_master),
            manager=_escalation_contact_dto(contacts.manager),
        )


class PodEscalationContactsUpdateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    scrum_master: EscalationContactUpdateDto | None = None
    manager: EscalationContactUpdateDto | None = None

    def to_choices(self) -> PodEscalationContactChoices:
        return PodEscalationContactChoices(
            scrum_master=_escalation_contact_choice(self.scrum_master),
            manager=_escalation_contact_choice(self.manager),
        )


def _escalation_contact_dto(contact: EscalationContact | None) -> EscalationContactDto | None:
    if contact is None:
        return None
    return EscalationContactDto(
        chat_external_id=contact.chat_external_id,
        display_name=contact.display_name,
        member_id=contact.member_id,
    )


def _escalation_contact_choice(
    dto: EscalationContactUpdateDto | None,
) -> EscalationContactChoice | None:
    if dto is None:
        return None
    return EscalationContactChoice(member_id=dto.member_id, chat_external_id=dto.chat_external_id)


class TenantWritebackResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool
    source: WriteBackGateSource

    @classmethod
    def from_domain(cls, gate: WriteBackGate) -> TenantWritebackResponse:
        return cls(enabled=gate.enabled, source=gate.source)


class TenantWritebackUpdateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool


class WritebackConsentResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    developer_id: str
    consent: WriteBackConsent

    @classmethod
    def from_domain(cls, preference: CheckInPreference) -> WritebackConsentResponse:
        return cls(
            developer_id=preference.developer_id,
            consent=preference.write_back_consent,
        )


class WritebackConsentUpdateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    consent: WriteBackConsent


class MyStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: StatusSource
    developer_confirmed: bool
    summary: str
    blockers: list[str]
    blocker_details: list[BlockerDetailDto] = Field(default_factory=list)
    eta_change_days: int | None
    status_as_of: date
    confirmed_at: datetime | None

    @classmethod
    def from_domain(
        cls,
        status: DeveloperStatus,
        blocker_details: tuple[BlockerDetailView, ...] = (),
    ) -> MyStatusResponse:
        return cls(
            source=status.source,
            developer_confirmed=status.developer_confirmed,
            summary=status.summary,
            blockers=list(status.blockers),
            blocker_details=[BlockerDetailDto.from_view(detail) for detail in blocker_details],
            eta_change_days=status.eta_change_days,
            status_as_of=status.as_of,
            confirmed_at=status.confirmed_at,
        )


class BlockerCorrectionItemDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    blocker_id: str | None = None
    description: str = Field(min_length=1, max_length=500)
    work_item_id: str | None = None
    pod_id: str | None = None
    resolved: bool = False

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("description must not be blank")
        return stripped


class StatusCorrectionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    summary: str = Field(min_length=1)
    blockers: list[str] = Field(default_factory=list)
    eta_change_days: int | None = None
    blocker_items: list[BlockerCorrectionItemDto] | None = Field(
        default=None,
        description=(
            "Structured blocker corrections. When present, this is the "
            "authoritative set; the flat blockers list is ignored."
        ),
    )

    @field_validator("summary")
    @classmethod
    def normalize_summary(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("summary must not be blank")
        return stripped

    @field_validator("blockers")
    @classmethod
    def normalize_blockers(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            stripped = item.strip()
            if not stripped or stripped in seen:
                continue
            seen.add(stripped)
            normalized.append(stripped)
        return normalized

    @field_validator("blocker_items")
    @classmethod
    def limit_blocker_items(
        cls, value: list[BlockerCorrectionItemDto] | None
    ) -> list[BlockerCorrectionItemDto] | None:
        if value is not None and len(value) > 20:
            raise ValueError("blocker_items must contain at most 20 items")
        return value


# Fields a person may not set on their own check-in preference, with the reason
# the self endpoint gives when a request carries one.
_NOT_SELF_SET_CHECKIN_FIELDS = {
    "reply_wait_seconds": (
        "is set by an admin: the reply windows decide when the scrum master and "
        "manager hear about a missed check-in"
    ),
    "final_reply_wait_seconds": (
        "is set by an admin: the reply windows decide when the scrum master and "
        "manager hear about a missed check-in"
    ),
    "local_time": "is not set per person: check-ins go out at one time for the whole team",
}


class SelfCheckinPreferenceUpdateRequest(BaseModel):
    """The part of their own check-in preference a person may change.

    Only the days they are asked and their time zone. The reply windows stay
    with an admin (``PUT /config/members/{id}/checkin-preference``) because they
    decide when a missed check-in reaches the scrum master and manager. The
    check-in time is left out because check-ins go out at one tenant-wide time,
    so a personal time would change nothing. A request carrying any other field
    is rejected, never silently ignored.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    timezone: str | None = None
    weekdays: list[int] | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_fields_not_set_by_self(cls, data: object) -> object:
        if isinstance(data, dict):
            refused = [
                f"{name} {reason}"
                for name, reason in _NOT_SELF_SET_CHECKIN_FIELDS.items()
                if name in data
            ]
            if refused:
                raise ValueError("; ".join(refused))
        return data

    @field_validator("weekdays")
    @classmethod
    def validate_weekdays(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return value
        if any(day < 0 or day > 6 for day in value):
            raise ValueError("weekdays must be in the range 0..6")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value
