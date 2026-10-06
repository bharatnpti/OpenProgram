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
