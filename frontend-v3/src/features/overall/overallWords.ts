// Pure wording for the Overall view's forecast and requirements, type imports
// only so `node --test` runs it directly.
import type { ReleaseCandidateResponse, ScopeDeliveryResponse } from "../../api/schema";

import { daysBetween } from "../../lib/format.ts";

/** "Release Checkout 1.0", but "Release 1.1" when the name already says it. */
export function releaseName(name: string): string {
  return /^release\b/i.test(name.trim()) ? name.trim() : `Release ${name.trim()}`;
}

/** "Last day", "Last 30 days": the timeline card's title for its snapshot count. */
export function timelineTitle(days: number): string {
  if (days <= 0) return "Daily snapshots";
  return days === 1 ? "Last day" : `Last ${days} days`;
}

/**
 * What the server would refuse about a delivery date, said first by the form.
 * Mirrors core/domain/forecast.py: a date more than a year back is not a plan.
 */
export function dateProblem(value: string, today: string): string | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return "Pick a date.";
  if (daysBetween(value, today) > 365) {
    return "A delivery date more than a year in the past is not a plan.";
  }
  return null;
}

/** True when the pod commits a date after the project's: worth saying out loud. */
export function podLaterThanProject(
  podDate: string | null | undefined,
  projectDate: string | null | undefined,
): boolean {
  return Boolean(podDate && projectDate && podDate > projectDate);
}

/**
 * The day the daily history starts, when the timeline shows all of it (fewer
 * days than the window asked for); null when older days may have been cut off.
 */
export function historyStart(timeline: { day: string }[], windowDays: number): string | null {
  return timeline.length > 0 && timeline.length < windowDays ? timeline[0].day : null;
}

/**
 * When a requirement entered its stage. The history can only say "since the
 * first snapshot" for one that has not moved since, so that day reads "or
 * earlier": it may have been there longer.
 */
export function inStageSinceWords(
  since: string | null,
  start: string | null,
  formatDay: (iso: string) => string,
): string {
  if (!since) return "—";
  return start !== null && since === start ? `${formatDay(since)} or earlier` : formatDay(since);
}

/** Whether a calendar day is before the day shown: a date that has already gone by. */
export function isPastDay(day: string | null | undefined, shown: string | null | undefined) {
  return Boolean(day && shown && day.slice(0, 10) < shown.slice(0, 10));
}

/**
 * The team's own dates, said once for the pod card and the Overall forecast:
 * "latest of 1 dated item (INS-4)", and whether that date has already gone by.
 */
export function teamDatesLine(
  team: { dated: number; undated: number; latest_key: string | null },
  past: boolean,
): string {
  const items = `${team.dated} dated ${team.dated === 1 ? "item" : "items"}`;
  const key = team.latest_key ? ` (${team.latest_key})` : "";
  const gone = past ? ", past its date" : "";
  const undated = team.undated > 0 ? ` · ${team.undated} without a date` : "";
  return `latest of ${items}${key}${gone}${undated}`;
}

/**
 * Why a project shows no requirements. Today it is a set-up matter (no Jira
 * status placed in a stage); on a past day it is only that nothing was kept
 * that far back, which no admin can change.
 */
export function noRequirementsWords(dayLabel: string | null): string {
  return dayLabel
    ? `No requirements were recorded for this project on ${dayLabel}. The history starts on the day its first daily snapshot was kept.`
    : "No requirements are counted for this project yet. An admin places Jira statuses in stages on the console's Admin → Delivery stages tab.";
}

/** Why a chart of daily snapshots is empty, for today or for a past day. */
export function noSnapshotsWords(dayLabel: string | null): string {
  return dayLabel
    ? `No daily snapshot goes back to ${dayLabel}. The history starts on a later day.`
    : "No daily snapshots yet. The burn-down starts once the first one is kept.";
}

/** A scope with no requirements counted has no verdict worth showing. */
export function inScope(scope: Pick<ScopeDeliveryResponse, "total">): boolean {
  return scope.total > 0;
}

/** "Checkout 1.0 · 4 issues · Jira releases Fri 30 Oct" for a candidate to pick. */
export function candidateLabel(
  candidate: ReleaseCandidateResponse,
  formatDay: (iso: string) => string,
): string {
  const issues = `${candidate.issues} ${candidate.issues === 1 ? "issue" : "issues"}`;
  const date = candidate.release_date
    ? ` · Jira releases ${formatDay(candidate.release_date)}`
    : "";
  return `${candidate.value} · ${issues}${date}`;
}
