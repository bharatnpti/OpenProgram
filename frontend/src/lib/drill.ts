import type { NodeKind } from "../api/schema";

// Maps a graph entity to its detail route, or null when no detail page exists
// for that kind (program/developer/task have no dedicated page today).
export function personaDrillPath(kind: NodeKind, id: string): string | null {
  switch (kind) {
    case "project":
      return `/projects/${encodeURIComponent(id)}`;
    case "pod":
      return `/pods/${encodeURIComponent(id)}`;
    case "workstream":
      return `/workstreams/${encodeURIComponent(id)}`;
    default:
      return null;
  }
}
