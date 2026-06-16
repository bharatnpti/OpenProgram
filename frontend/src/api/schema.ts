export type NodeKind = "program" | "project" | "pod" | "developer" | "task";
export type EdgeKind = "contains" | "assigned_to" | "depends_on";

export interface HealthResponse {
  status: string;
  environment: string;
  tenant_id: string;
  correlation_id: string;
}

export interface ReadyResponse {
  status: string;
  dependencies: Record<string, boolean>;
}

export interface GraphNodeDto {
  id: string;
  kind: NodeKind;
  name: string;
  metadata: Record<string, string | number | boolean | null>;
}

export interface GraphEdgeDto {
  from_node_id: string;
  to_node_id: string;
  kind: EdgeKind;
  valid_from: string | null;
  valid_to: string | null;
}

export interface GraphTreeDto {
  root: GraphNodeDto;
  nodes: GraphNodeDto[];
  edges: GraphEdgeDto[];
}
