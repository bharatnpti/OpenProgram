// Early warnings grouped by project, pure, from the one bulk read
// (`GET /portfolio/warnings`): the worst project first, and in each the worst
// and oldest warning first. Type imports, and runtime imports by their .ts
// path, so `node --test` runs it as written.
import type { PortfolioWarningsResponse, Rag, WarningItemDto, WarningKind } from "../../api/schema";
import { plural } from "../../lib/words.ts";

export type ProjectRow = WarningItemDto & { group: WarningKind };

export type ProjectWarnings = {
  projectId: string;
  name: string;
  worst: Rag;
  items: ProjectRow[];
};

const SEVERITY: Record<Rag, number> = { red: 3, amber: 2, unknown: 1, green: 0 };

/**
 * Every listed warning, under its project. A group the reader reads only as a
 * count has no items to place, so it stays out of the projects and is counted
 * apart (`countedApart`).
 */
export function byProject(view: Pick<PortfolioWarningsResponse, "groups">): ProjectWarnings[] {
  const projects = new Map<string, ProjectWarnings>();
  for (const group of view.groups) {
    if (group.counts_only) continue;
    for (const item of group.items) {
      let project = projects.get(item.project_id);
      if (!project) {
        project = {
          projectId: item.project_id,
          name: item.project_name,
          worst: "green",
          items: [],
        };
        projects.set(item.project_id, project);
      }
      project.items.push({ ...item, group: group.kind });
      if (SEVERITY[item.severity] > SEVERITY[project.worst]) project.worst = item.severity;
    }
  }
  const list = [...projects.values()];
  list.forEach((project) =>
    project.items.sort(
      (a, b) => SEVERITY[b.severity] - SEVERITY[a.severity] || b.age_days - a.age_days,
    ),
  );
  return list.sort(
    (a, b) =>
      SEVERITY[b.worst] - SEVERITY[a.worst] ||
      b.items.length - a.items.length ||
      a.name.localeCompare(b.name),
  );
}

/** "7 blockers people reported are counted, not listed, for you": the groups read as counts. */
export function countedApart(view: Pick<PortfolioWarningsResponse, "groups">): string | null {
  const counted = view.groups.filter((group) => group.counts_only && group.count > 0);
  if (counted.length === 0) return null;
  const parts = counted.map((group) => `${group.count} ${group.title.toLowerCase()}`);
  return `Also ${parts.join(" and ")}, counted across the projects rather than listed.`;
}

/** A warning's kind in two or three words, beside it in a project's list. */
export const KIND_WORDS: Record<WarningKind, string> = {
  blockers: "Blocker",
  tools_disagree: "Tools disagree",
  done_in_code: "Done in code",
  review_stuck: "Review stuck",
  no_movement: "No movement",
  no_code_yet: "No code yet",
};

/** Drift: what Jira and Git say against what was reported, or against each other. */
export function isDrift(kind: WarningKind): boolean {
  return kind === "tools_disagree" || kind === "done_in_code";
}

/** "3 warnings", a project's count in its heading. */
export function projectCount(project: Pick<ProjectWarnings, "items">): string {
  return plural(project.items.length, "warning", "warnings");
}
