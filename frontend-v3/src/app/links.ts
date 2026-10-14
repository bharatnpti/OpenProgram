// Where each thing opens in the console, in one place: one Project
// page and one Pod page, each at its stable id, the Portfolio for a program.
// Every link the console draws comes from here, so no screen emits an old
// route. No imports, so `node --test` runs it as written.

/** A project's page: `/projects/<id>`, the stable id, never a slug. */
export function projectPath(projectId: string, hash?: string): string {
  return `/projects/${encodeURIComponent(projectId)}${hash ? `#${hash}` : ""}`;
}

/** A pod's page: `/pods/<id>`. */
export function podPath(podId: string, hash?: string): string {
  return `/pods/${encodeURIComponent(podId)}${hash ? `#${hash}` : ""}`;
}

/** A program is a heading on the Portfolio: `/portfolio?program=<id>`. */
export function programPath(programId: string): string {
  return `/portfolio?program=${encodeURIComponent(programId)}`;
}

/** A project's day report: `/reports/<id>/daily`, the link sent day reports carry. */
export function dailyPath(projectId: string): string {
  return `/reports/${encodeURIComponent(projectId)}/daily`;
}

/**
 * A workstream has no page of its own: it opens in its first project's Work
 * section. Without a project it is the old address, which the router resolves.
 */
export function workstreamPath(workstreamId: string, projectIds: readonly string[]): string {
  return projectIds[0]
    ? projectPath(projectIds[0], "work")
    : `/delivery/workstream/${encodeURIComponent(workstreamId)}`;
}

/** Any node of the graph, where it opens. */
export function nodePath(
  kind: "program" | "project" | "workstream" | "pod",
  id: string,
  projectIds: readonly string[] = [],
): string {
  if (kind === "program") return programPath(id);
  if (kind === "project") return projectPath(id);
  if (kind === "pod") return podPath(id);
  return workstreamPath(id, projectIds);
}
