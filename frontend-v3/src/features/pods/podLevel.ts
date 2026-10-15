// The pod level of a project: one pod's part of the date, its warnings, its scope
// and its reports. Pure: who reads which part, and the pod's part of the plan in
// words. Type imports only, and runtime imports by their .ts path, so
// `node --test` runs it as written.
import type { AgainstPlanDto } from "../../api/schema";
import type { AppRole } from "../../app/roleWords";
import { formatDay } from "../../lib/format.ts";
import { plural } from "../../lib/words.ts";

/**
 * What a reader gets at a pod's level, by role, mirroring the server's reads
 * (the server still decides). `own`: the reader reads the pod's own detail
 * (`usePodAccess`'s `full`): every pod for a manager or an admin, their own for
 * a scrum master or a developer.
 *
 * - `morning`: the pod's own detail, its blockers, today's status of its tasks
 *   and why it has its colour: a manager and an admin every pod, a scrum master
 *   and a developer their own;
 * - `warnings`: its early warnings: everyone who reads the project's, and a
 *   developer their own pod's, in counts and teams;
 * - `projectTimeline`: its row on the project's timeline; a developer reads the
 *   pod's own timeline instead;
 * - `requirements`: requirements by stage below it, the project's progress readers;
 * - `scope`: Scope control, every reader of it but a scrum master's other pods;
 * - `readiness`: the pod's own readiness board: a manager, an admin, a scrum
 *   master the pods they run;
 * - `reports`: the pod's daily briefs; a developer reads none;
 * - `flow`: its review flow.
 */
export type PodReads = {
  morning: boolean;
  warnings: boolean;
  projectTimeline: boolean;
  requirements: boolean;
  scope: boolean;
  readiness: boolean;
  reports: boolean;
  flow: boolean;
};

export function podReads(lens: readonly AppRole[], own: boolean): PodReads {
  const has = (role: AppRole) => lens.includes(role);
  const every = has("admin") || has("mgr");
  const progress = every || has("po") || has("exec");
  const sm = has("sm");
  const dev = has("dev");
  return {
    morning: every || (own && (sm || dev)),
    warnings: every || progress || sm || (own && dev),
    projectTimeline: progress || sm,
    requirements: progress,
    scope: progress || (sm && own),
    readiness: every || (sm && own),
    reports: progress || sm,
    flow: progress || sm,
  };
}

/**
 * The pod's part of the plan in one line: "4 planned on Tue 26 May · 1 added
 * after, 1 without the CR label · none removed".
 */
export function podPlanLine(plan: AgainstPlanDto): string {
  const planned = `${plural(plan.planned, "requirement", "requirements")} planned on ${formatDay(plan.planned_on)}`;
  const added =
    plan.added === 0
      ? "nothing added after"
      : `${plan.added} added after${
          plan.without_label > 0
            ? `, ${plan.without_label} without the ${plan.change_label} label`
            : ""
        }`;
  const removed = plan.removed === 0 ? "none removed" : `${plan.removed} removed`;
  return `${planned} · ${added} · ${removed}`;
}

/** What each of a pod's Scope control sections is, in the tab's one line. */
const SCOPE_WORDS: Record<string, string> = {
  baseline: "part of the plan",
  gates: "acceptance gates",
  readiness: "readiness before production",
  hygiene: "Jira hygiene",
};

/**
 * The pod's Scope control in one line, from the sections drawn for this reader:
 * "Payments Pod's part of the plan, acceptance gates and Jira hygiene."
 */
export function podScopeLine(pod: string, drawn: readonly string[]): string {
  const words = drawn.map((id) => SCOPE_WORDS[id]).filter((word): word is string => Boolean(word));
  if (words.length === 0) return `Nothing of ${pod}'s scope is yours to read.`;
  const list =
    words.length === 1
      ? words[0]
      : `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
  return `${pod}'s ${list}.`;
}
