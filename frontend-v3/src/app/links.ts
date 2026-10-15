// Where each thing opens in the console, in one place: the Projects hub,
// at All projects (`/portfolio/<tab>`), one project (`/projects/<id>/<tab>`)
// or one of its pods (`/projects/<id>/pods/<pod>/<tab>`), each at its stable
// id. Every link the console draws comes from here, so no screen emits an old
// route. Runtime imports by their .ts path only, so `node --test` runs it.
import { placePath, tabOfAnchor, type HubPlace, type HubTab } from "../features/hub/hubPaths.ts";

function withHash(path: string, hash?: string): string {
  return hash ? `${path}#${hash}` : path;
}

/**
 * A project: `/projects/<id>`, the stable id, never a slug. A section's anchor
 * opens the tab it lives on (`timeline` the date, `warnings` Early warnings,
 * `gates` Scope control); with none, the role's first tab there.
 */
export function projectPath(projectId: string, hash?: string): string {
  const tab = hash ? tabOfAnchor("project", hash) : null;
  return withHash(placePath({ level: "project", projectId }, tab), hash);
}

/** One tab of a project, with an anchor on it when given. */
export function projectTabPath(projectId: string, tab: HubTab, hash?: string): string {
  return withHash(placePath({ level: "project", projectId }, tab), hash);
}

/**
 * A pod, its part of a project: `/projects/<project>/pods/<id>/<tab>`. A pod's
 * morning (`checkins`, `blockers`, `status`) is on Early warnings, the rest on
 * the date. Without a project it is the old `/pods/<id>`, which the router
 * places on the viewer's own project of the pod, else its first.
 */
export function podPath(podId: string, hash?: string, projectId?: string | null): string {
  if (!projectId) return withHash(`/pods/${encodeURIComponent(podId)}`, hash);
  const tab = (hash ? tabOfAnchor("pod", hash) : null) ?? "date";
  return withHash(placePath({ level: "pod", projectId, podId }, tab), hash);
}

/** One tab of a pod's part of a project. */
export function podTabPath(projectId: string, podId: string, tab: HubTab, hash?: string): string {
  return withHash(placePath({ level: "pod", projectId, podId }, tab), hash);
}

/** All projects at a tab: `/portfolio/<tab>`; with none, the role's first tab there. */
export function portfolioPath(tab?: HubTab): string {
  return placePath({ level: "all" }, tab);
}

/** Any place of the hub at a tab. */
export function hubPath(place: HubPlace, tab?: HubTab | null): string {
  return placePath(place, tab);
}

/** A program is a heading on All projects' date: `/portfolio/date?program=<id>`. */
export function programPath(programId: string): string {
  return `${portfolioPath("date")}?program=${encodeURIComponent(programId)}`;
}

/**
 * A project's day reports, on its Reports tab: `/projects/<id>/reports`, with
 * one report picked (`?report=`). The links sent in day reports before the
 * tabs (`/reports/<id>/daily`) are placed here by the router.
 */
export function dailyPath(projectId: string, reportId?: string): string {
  const path = projectTabPath(projectId, "reports");
  return reportId ? `${path}?report=${encodeURIComponent(reportId)}` : path;
}

/**
 * A workstream has no page of its own: its row in its first project's date,
 * under Workstreams (`?workstream=<id>#work`). Without a project it is the old
 * address, which the router resolves.
 */
export function workstreamPath(workstreamId: string, projectIds: readonly string[]): string {
  return projectIds[0]
    ? `${projectTabPath(projectIds[0], "date")}?workstream=${encodeURIComponent(workstreamId)}#work`
    : `/delivery/workstream/${encodeURIComponent(workstreamId)}`;
}

/** Any node of the graph, where it opens; a pod on its first project when one is given. */
export function nodePath(
  kind: "program" | "project" | "workstream" | "pod",
  id: string,
  projectIds: readonly string[] = [],
): string {
  if (kind === "program") return programPath(id);
  if (kind === "project") return projectPath(id);
  if (kind === "pod") return podPath(id, undefined, projectIds[0]);
  return workstreamPath(id, projectIds);
}
