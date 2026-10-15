// The switcher's navigator tree, as rows: All projects first (for the readers
// of project lists), then Yours and Recent, then programme › project › pods,
// each project with its verdict and, under it, its pods. Programme groups
// after the first are folded; a search or a verdict chip unfolds them, and a
// search that names a pod opens its project. Pure, with type imports and
// runtime imports by their .ts path, so `node --test` runs it.
import type { Verdict } from "../../api/schema";
import { matchesQuery, switcherSections, type ProjectOption } from "../project/projectChoice.ts";
import type { PodChoice } from "./hubTabs.ts";

/** A project as the tree lists it: its verdict, its pods and the workstreams in use on it. */
export type TreeProject = ProjectOption & {
  /** The date verdict from the bulk read; null where none is kept or read. */
  verdict: Verdict | null;
  pods: PodChoice[];
  /** The names of the workstreams in use on it, for one muted line. */
  workstreams: string[];
};

/** The chips that narrow the tree to one answer of the date. */
export type VerdictChip = "off_track" | "at_risk" | "no_date";

export const VERDICT_CHIPS: readonly { value: VerdictChip; label: string }[] = [
  { value: "off_track", label: "Off track" },
  { value: "at_risk", label: "At risk" },
  { value: "no_date", label: "No date" },
];

export type TreeRow =
  | { kind: "all"; key: "all" }
  | {
      kind: "heading";
      key: string;
      label: string;
      /** A programme group can be folded; Yours and Recent cannot. */
      foldable: boolean;
      folded: boolean;
      count: number;
    }
  | {
      kind: "project";
      key: string;
      project: TreeProject;
      /** Its programme's name, when the section it sits in is not that programme. */
      programme: string | null;
      /** Its pods are listed under it. */
      expanded: boolean;
    }
  | { kind: "pod"; key: string; pod: PodChoice; project: TreeProject };

/** Whether a row is one a pick opens (the arrow keys stop on it). */
export function opens(row: TreeRow): boolean {
  return row.kind !== "heading";
}

export type TreeState = {
  query: string;
  chips: readonly VerdictChip[];
  /** Programme groups the person folded or unfolded, by section id, against the default. */
  toggledGroups: ReadonlySet<string>;
  /** Projects whose pods are open, by id. */
  expanded: ReadonlySet<string>;
  /** Draw the All projects row (the readers of project lists). */
  withAll: boolean;
};

/** Every word of the query in a pod's name. */
function podMatches(pod: PodChoice, query: string): boolean {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const name = pod.name.toLowerCase();
  return words.length > 0 && words.every((word) => name.includes(word));
}

/**
 * The tree's rows for what is typed and picked. A project matches by its name,
 * programme or Jira key, or by one of its pods' names; one found by a pod
 * opens with only the pods that matched. A chip keeps the projects with that
 * verdict. Each project is listed once, in the first section that has it.
 */
export function treeRows(
  projects: readonly TreeProject[],
  recent: readonly string[],
  programs: { id: string; name: string }[],
  state: TreeState,
): TreeRow[] {
  const query = state.query.trim();
  const programName = (id: string) => programs.find((program) => program.id === id)?.name;
  const byId = new Map(projects.map((project) => [project.id, project]));
  const viaPods = new Set<string>();
  const kept = projects.filter((project) => {
    if (
      state.chips.length > 0 &&
      !(project.verdict && state.chips.includes(project.verdict as VerdictChip))
    ) {
      return false;
    }
    if (!query || matchesQuery(project, query, programName)) return true;
    if (project.pods.some((pod) => podMatches(pod, query))) {
      viaPods.add(project.id);
      return true;
    }
    return false;
  });
  const narrowing = query !== "" || state.chips.length > 0;
  const sections = switcherSections([...kept], recent, programs);
  const rows: TreeRow[] = [];
  if (state.withAll && !narrowing) rows.push({ kind: "all", key: "all" });
  let programmeGroups = 0;
  for (const section of sections) {
    const isProgramme = section.id.startsWith("program:");
    const foldedByDefault = isProgramme && programmeGroups > 0;
    if (isProgramme) programmeGroups += 1;
    const foldable = isProgramme && !narrowing;
    const folded = foldable && foldedByDefault !== state.toggledGroups.has(section.id);
    rows.push({
      kind: "heading",
      key: `heading:${section.id}`,
      // "All projects" is the row above; a list with no programmes is just its projects.
      label: section.label === "All projects" ? "Projects" : section.label,
      foldable,
      folded,
      count: section.options.length,
    });
    if (folded) continue;
    for (const option of section.options) {
      const project = byId.get(option.id);
      if (!project) continue;
      const found = viaPods.has(project.id);
      const expanded = found || state.expanded.has(project.id);
      const programme = section.id.startsWith("program:")
        ? null
        : (programName(project.programIds[0] ?? "") ?? null);
      rows.push({
        kind: "project",
        key: `${section.id}/project:${project.id}`,
        project,
        programme,
        expanded: expanded && project.pods.length > 0,
      });
      if (!expanded) continue;
      const pods = found ? project.pods.filter((pod) => podMatches(pod, query)) : project.pods;
      for (const pod of pods) {
        rows.push({ kind: "pod", key: `${section.id}/pod:${project.id}:${pod.id}`, pod, project });
      }
    }
  }
  return rows;
}

/** A row's height in the list, in pixels, for drawing only the rows near the screen. */
export function rowHeight(row: TreeRow): number {
  if (row.kind === "heading") return 34;
  if (row.kind === "project") return row.project.workstreams.length > 0 ? 62 : 46;
  return 42;
}
