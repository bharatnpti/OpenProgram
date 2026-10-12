// Planning baseline: the requirements agreed when planning was complete, and how
// the scope moved since, in words; and the Planning complete form's own checks.
// Pure wording; runtime imports by their .ts path, so `node --test` runs it as written.
import type {
  PlanningBaselineResponse,
  PlanningBaselinesResponse,
  PlanningCompleteRequest,
} from "../../api/schema";

import { formatDay } from "../../lib/format.ts";

type Baseline = Pick<
  PlanningBaselineResponse,
  | "number"
  | "set_at"
  | "set_by_name"
  | "planned_on"
  | "requirements"
  | "open"
  | "points"
  | "change_label"
  | "since"
  | "reconstructed"
  | "snapshot_day"
  | "superseded_at"
>;

/** The most keys a line names before "and N more". */
const MAX_KEYS = 8;
const MAX_LABEL = 64;
const MAX_GRACE = 20;
const MAX_DAYS_BACK = 365;

const plural = (count: number, one: string, many = `${one}s`) =>
  `${count} ${count === 1 ? one : many}`;

/** "Baseline 2, set Wed 14 Oct by Mina Patel." */
export function baselineHeadline(baseline: Baseline): string {
  return `Baseline ${baseline.number}, set ${formatDay(baseline.set_at)} by ${baseline.set_by_name}.`;
}

/**
 * What was frozen: "Planning complete on Thu 1 Oct: 24 requirements, 18 not yet in
 * production, 120 story points."
 */
export function frozenWords(baseline: Baseline): string {
  const parts = [plural(baseline.requirements, "requirement")];
  if (baseline.open < baseline.requirements) {
    parts.push(`${baseline.open} not yet in production`);
  }
  if (baseline.points !== null && baseline.points !== undefined) {
    parts.push(`${baseline.points} story ${baseline.points === 1 ? "point" : "points"}`);
  }
  return `Planning complete on ${formatDay(baseline.planned_on)}: ${parts.join(", ")}.`;
}

/**
 * How the scope moved since the baseline, by count:
 * "Since then: 3 added (2 without the CR label) and 1 removed." The label count is
 * said only for what was added; a key is never tied to a person here.
 */
export function sinceWords(baseline: Baseline, prefix = "Since then"): string {
  const { added, added_with_label: labelled, removed } = baseline.since;
  if (added === 0 && removed === 0) return `${prefix}: nothing added or removed.`;
  const parts: string[] = [];
  if (added > 0) parts.push(`${added} added${labelWords(added, labelled, baseline.change_label)}`);
  if (removed > 0) parts.push(`${removed} removed`);
  return `${prefix}: ${parts.join(" and ")}.`;
}

function labelWords(added: number, labelled: number, label: string): string {
  if (labelled === 0) return ` (none with the ${label} label)`;
  if (labelled === added) return ` (${added === 1 ? "with" : "all with"} the ${label} label)`;
  return ` (${added - labelled} without the ${label} label)`;
}

/** "CHK-20, CHK-21 (CR) and 3 more", from the keys the read lists and the whole count. */
export function keyList(
  keys: string[],
  total: number,
  labelled: string[] = [],
  label = "",
): string {
  const shown = keys
    .slice(0, MAX_KEYS)
    .map((key) => (labelled.includes(key) && label ? `${key} (${label})` : key));
  const more = total - shown.length;
  if (more <= 0) return shown.join(", ");
  return `${shown.join(", ")} and ${more} more`;
}

/** Why the frozen set is not the day's own snapshot, when it is not; null otherwise. */
export function sourceNote(baseline: Baseline): string | null {
  if (baseline.reconstructed) {
    return "Rebuilt afterwards: no snapshot of this scope was kept that day, so it stands on the issues as the tracker lists them.";
  }
  if (baseline.snapshot_day) {
    return `Read from the snapshot of ${formatDay(baseline.snapshot_day)}, the last one kept before that day.`;
  }
  return null;
}

/** An earlier baseline in one line: "Baseline 1, set Mon 14 Sep by Mina Patel: 20 requirements; replaced Wed 14 Oct." */
export function earlierLine(baseline: Baseline): string {
  const replaced = baseline.superseded_at ? `; replaced ${formatDay(baseline.superseded_at)}` : "";
  return `Baseline ${baseline.number}, set ${formatDay(baseline.set_at)} by ${baseline.set_by_name}: ${plural(baseline.requirements, "requirement")}${replaced}.`;
}

/** The section's line when no baseline exists yet. */
export function noBaselineWords(
  read: Pick<PlanningBaselinesResponse, "name" | "release_id">,
): string {
  const subject = read.release_id ? read.name : "this project";
  return `Planning is not marked complete for ${subject} yet. Once it is, the requirements in scope are frozen, and any that join later show as added scope.`;
}

// ---- The form -------------------------------------------------------------------------

export type PlanningForm = {
  /** Planning complete now, or on an earlier day. */
  when: "now" | "earlier";
  /** The earlier day, YYYY-MM-DD, or "". */
  day: string;
  label: string;
  grace: string;
  note: string;
};

export function emptyPlanningForm(defaults: { label: string; grace: number }): PlanningForm {
  return { when: "now", day: "", label: defaults.label, grace: String(defaults.grace), note: "" };
}

/** The form's first problem in words, or null; the server checks the same again. */
export function planningProblem(form: PlanningForm, today: string): string | null {
  if (form.when === "earlier") {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(form.day)) return "Pick the day planning was complete.";
    if (form.day > today) return "Pick today or an earlier day.";
    if (daysBack(form.day, today) > MAX_DAYS_BACK) return "Pick a day within the last year.";
  }
  const label = form.label.trim();
  if (/\s/.test(label)) return "A change label is one Jira label, with no spaces.";
  if (label.length > MAX_LABEL) return `A change label is at most ${MAX_LABEL} characters.`;
  const grace = form.grace.trim();
  if (!/^\d+$/.test(grace) || Number(grace) > MAX_GRACE) {
    return `The grace period is 0 to ${MAX_GRACE} working days.`;
  }
  return null;
}

/** The request the form sends: an empty label leaves the default to the server. */
export function planningBody(form: PlanningForm, releaseId: string): PlanningCompleteRequest {
  const label = form.label.trim();
  return {
    release_id: releaseId || null,
    planned_on: form.when === "earlier" ? form.day : null,
    change_label: label || null,
    grace_working_days: Number(form.grace.trim()),
    note: form.note.trim(),
  };
}

function daysBack(day: string, today: string): number {
  return Math.round(
    (Date.parse(`${today}T12:00:00Z`) - Date.parse(`${day}T12:00:00Z`)) / 86_400_000,
  );
}
