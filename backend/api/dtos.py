from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.domain.directory import DirectoryUser
from core.application.config_service import DirectoryItemView
from core.application.persona_views import (
    BlockerView,
    CheckinDeveloperView,
    FocusItemView,
    FocusTaskView,
    FocusView,
    HeatmapCellView,
    PodBlockersView,
    PodCheckinsView,
    PortfolioHeatmapView,
    ProgramTreeView,
    ProjectProgressView,
    TaskProgressView,
    TreeEdgeView,
    TreeNodeView,
)
from core.domain.graph import EdgeKind, GraphEdge, GraphNode, GraphTree, NodeKind
from core.domain.rollup import Rag, RollupFactor
from core.domain.status import CheckInPreference, StatusSource


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
            metadata=metadata,
        )


class ConfigNodeCreateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None
    code: str | None = None
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class ConfigNodeUpdateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    code: str | None = None
    metadata: dict[str, str | int | float | bool | None] | None = None


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
    pod_ids: list[str]
    member_ids: list[str]
    task_ids: list[str]

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
            pod_ids=list(view.pod_ids),
            member_ids=list(view.member_ids),
            task_ids=list(view.task_ids),
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


class EntityRefDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    kind: NodeKind
    id: str


class RollupFactorDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    description: str
    contributes: Rag
    source_ref: EntityRefDto

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


class FocusResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    developer_id: str
    developer_name: str
    as_of: date
    status_source: StatusSource
    status_as_of: date | None
    summary: str
    blockers: list[str]
    tasks: list[FocusTaskDto]
    focus: list[FocusItemDto]

    @classmethod
    def from_view(cls, view: FocusView) -> FocusResponse:
        return cls(
            developer_id=view.developer_id,
            developer_name=view.developer_name,
            as_of=view.as_of,
            status_source=view.status_source,
            status_as_of=view.status_as_of,
            summary=view.summary,
            blockers=list(view.blockers),
            tasks=[FocusTaskDto.from_view(task) for task in view.tasks],
            focus=[FocusItemDto.from_view(item) for item in view.focus],
        )


class BlockerDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    description: str
    age_days: int
    owner_id: str
    owner_name: str
    source: StatusSource
    status_as_of: date
    source_ref: EntityRefDto

    @classmethod
    def from_view(cls, blocker: BlockerView) -> BlockerDto:
        return cls(
            id=blocker.id,
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
    state: Literal["confirmed", "stale", "missing"]
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
            stale=view.stale,
            missing=view.missing,
            developers=[CheckinDeveloperDto.from_view(item) for item in view.developers],
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


class WorkflowDispatchResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    workflow_id: str


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


class CheckinPreferenceUpdateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    local_time: time | None = None
    timezone: str | None = None
    weekdays: list[int] | None = None
    reply_wait_seconds: int | None = Field(default=None, ge=0)
    final_reply_wait_seconds: int | None = Field(default=None, ge=0)

    @field_validator("weekdays")
    @classmethod
    def validate_weekdays(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return value
        if any(day < 0 or day > 6 for day in value):
            raise ValueError("weekdays must be in the range 0..6")
        return value
