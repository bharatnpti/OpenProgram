from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict

from core.domain.graph import EdgeKind, GraphEdge, GraphNode, GraphTree, NodeKind


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


class ChatWebhookResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    message_id: str
