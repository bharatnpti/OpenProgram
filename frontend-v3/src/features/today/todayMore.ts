// Pure words for what Today adds per role: the portfolio line, the oldest
// warnings, the scrum master's other pods, the product owner's other projects
// and the baseline and hygiene actions waiting on them, and a developer's next
// tasks. Type imports and extensioned imports only, so `node --test` runs it.
import type {
  PortfolioWarningsResponse,
  ScopeProjectDto,
  VerdictCountsDto,
  WarningItemDto,
  WarningKind,
} from "../../api/schema";
import { plural } from "../../lib/words.ts";
import type { TaskRow } from "./taskUpdate";

/** How many oldest warnings, and how many next tasks, Today lists. */
export const TODAY_SHOWN = 3;

/**
 * "Portfolio: 1 off track · 0 at risk · 2 no date": the projects' verdicts in
 * one line under the worst project's answer, the three that ask for a look
 * said with their count even when it is 0, so none reads apart from not
 * counted.
 */
export function portfolioLine(counts: VerdictCountsDto): string {
  return `Portfolio: ${counts.off_track ?? 0} off track · ${counts.at_risk ?? 0} at risk · ${counts.no_date ?? 0} no date`;
}

/**
 * The oldest open warnings across the reader's projects, oldest first, at most
 * `limit`: every listed group's items (a group read only as a count has none
 * to list), each with its kind.
 */
export function oldestWarnings(
  view: Pick<PortfolioWarningsResponse, "groups">,
  limit = TODAY_SHOWN,
): (WarningItemDto & { group: WarningKind; title: string })[] {
  const seen = new Set<string>();
  return view.groups
    .filter((group) => !group.counts_only)
    .flatMap((group) =>
      group.items.map((item) => ({ ...item, group: group.kind, title: group.title })),
    )
    .filter((item) => {
      if (seen.has(item.id)) return false;
      seen.add(item.id);
      return true;
    })
    .sort((a, b) => b.age_days - a.age_days || a.key.localeCompare(b.key))
    .slice(0, limit);
}

/**
 * One of a scrum master's other pods, in a line: "4 of 6 replied · 2 open
 * blockers". Nothing about a count that is not read yet.
 */
export function podLineWords(input: {
  replied: number | null;
  people: number | null;
  blockers: number | null;
}): string {
  const parts = [
    input.replied !== null && input.people !== null && input.people > 0
      ? `${input.replied} of ${input.people} replied`
      : input.people === 0
        ? "nobody asked to check in"
        : null,
    input.blockers !== null
      ? input.blockers === 0
        ? "no open blockers"
        : plural(input.blockers, "open blocker", "open blockers")
      : null,
  ];
  return parts.filter((part): part is string => Boolean(part)).join(" · ");
}

/** A baseline or hygiene action waiting on a product owner, for "Needs your attention". */
export type ScopeAction = {
  key: string;
  projectId: string;
  project: string;
  text: string;
  /** Where on the project's Scope control it is done. */
  anchor: "baseline" | "hygiene";
};

/**
 * What a product owner's own projects ask of them on Scope control, worst
 * first: a project with no planning baseline yet (Mark planning complete),
 * requirements that joined after planning without the change label (label
 * them or take a new baseline), then the Jira hygiene findings still open.
 * Projects with none of these are left out.
 */
export function scopeActions(
  projects: readonly ScopeProjectDto[],
  own: ReadonlySet<string>,
  readsBaselines: boolean,
): ScopeAction[] {
  const mine = projects.filter((project) => own.has(project.id));
  const actions: ScopeAction[] = [];
  for (const project of mine) {
    if (readsBaselines && !project.plan) {
      actions.push({
        key: `${project.id}:plan`,
        projectId: project.id,
        project: project.name,
        text: "No planning baseline yet: mark planning complete to count what joins after.",
        anchor: "baseline",
      });
    } else if (readsBaselines && project.plan && project.plan.without_label > 0) {
      const label = project.plan.change_label;
      actions.push({
        key: `${project.id}:label`,
        projectId: project.id,
        project: project.name,
        text: `${plural(project.plan.without_label, "requirement", "requirements")} joined after planning without the ${label} label: label ${project.plan.without_label === 1 ? "it" : "them"} or take a new baseline.`,
        anchor: "baseline",
      });
    }
  }
  for (const project of mine) {
    if (project.hygiene_open > 0) {
      actions.push({
        key: `${project.id}:hygiene`,
        projectId: project.id,
        project: project.name,
        text: `${plural(project.hygiene_open, "Jira hygiene finding", "Jira hygiene findings")} open: fix, waive or re-check.`,
        anchor: "hygiene",
      });
    }
  }
  return actions;
}

/** A developer's next tasks: the open ones in the order the task list sorts them, at most three. */
export function nextTasks(rows: readonly TaskRow[], limit = TODAY_SHOWN): TaskRow[] {
  return rows.filter((row) => row.group !== "done").slice(0, limit);
}

/** Why a next task is first: "blocked", "past due", "ETA after due", else its tracker status. */
export function nextTaskWhy(row: TaskRow): string {
  if (row.group === "blocked") return "Blocked";
  if (row.group === "past_due") return "Past due";
  if (row.group === "eta_late") return "Your ETA is after its due date";
  return row.task.tracker_status ?? (row.group === "in_progress" ? "In progress" : "To do");
}
