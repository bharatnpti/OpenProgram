import ReactFlow, {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  Position,
  type Edge,
  type Node,
} from "reactflow";
import "reactflow/dist/style.css";
import { useMemo } from "react";

import type { NodeKind, ProgramTreeResponse, Rag } from "../../api/schema";

const levelByKind: Record<NodeKind, number> = {
  program: 0,
  repo: 0,
  project: 1,
  workstream: 2,
  sprint: 2,
  pod: 2,
  developer: 3,
  task: 4,
  work_item: 4,
};

const nodeColors: Record<Rag | "none", string> = {
  none: "#eef2f6",
  unknown: "#d7dde5",
  green: "#dff3e8",
  amber: "#f8e6c8",
  red: "#f5d1d1",
};

export function HierarchyFlow({ data }: { data: ProgramTreeResponse | undefined }) {
  const { nodes, edges } = useMemo(() => buildFlow(data), [data]);

  if (!data) {
    return (
      <div className="flex h-[28rem] items-center justify-center text-sm text-muted-foreground">
        Loading hierarchy...
      </div>
    );
  }

  return (
    <div className="h-[28rem] w-full min-w-0 overflow-hidden rounded-md border border-border">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        nodesDraggable={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background color="#d7dde5" gap={18} />
        <MiniMap pannable={false} zoomable={false} nodeStrokeWidth={2} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}

function buildFlow(data: ProgramTreeResponse | undefined): { nodes: Node[]; edges: Edge[] } {
  if (!data) {
    return { nodes: [], edges: [] };
  }
  const byLevel = new Map<number, number>();
  const nodes = data.nodes.map((item) => {
    const level = levelByKind[item.kind];
    const offset = byLevel.get(level) ?? 0;
    byLevel.set(level, offset + 1);
    return {
      id: item.id,
      position: { x: offset * 210, y: level * 118 },
      sourcePosition: Position.Bottom,
      targetPosition: Position.Top,
      data: {
        label: (
          <div className="min-w-36">
            <div className="text-[11px] uppercase text-muted-foreground">{item.kind}</div>
            <div className="truncate text-sm font-semibold">{item.name}</div>
            <div className="mt-1 text-xs text-muted-foreground">
              {item.rag ?? "none"} / {item.source ?? "unknown"}
            </div>
          </div>
        ),
      },
      style: {
        border: "1px solid #b8c3cf",
        borderRadius: 6,
        background: nodeColors[item.rag ?? "none"],
        color: "#17202a",
        width: 168,
        padding: 8,
      },
    } satisfies Node;
  });
  const edges = data.edges.map(
    (edge) =>
      ({
        id: `${edge.from_node_id}-${edge.to_node_id}-${edge.kind}`,
        source: edge.from_node_id,
        target: edge.to_node_id,
        markerEnd: { type: MarkerType.ArrowClosed },
        style: { stroke: "#8b98a7" },
      }) satisfies Edge,
  );
  return { nodes, edges };
}
