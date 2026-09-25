import type { NarrativeBriefResponse } from "../api/schema";

/** The newest brief for each scope on each day it was generated.
 *
 * A seeded brief and the worker's own cron run both land on the same day, so
 * the same pod or project appeared twice with slightly different wording.
 * Keeping the newest per scope per day preserves the day-by-day record that
 * makes the column read like a real log, without the duplicate.
 *
 * Days are the *local* days the UI prints, so grouping can never disagree with
 * the dates on screen.
 */
export function latestBriefPerScopePerDay(
  briefs: readonly NarrativeBriefResponse[],
): NarrativeBriefResponse[] {
  const newest = new Map<string, NarrativeBriefResponse>();
  for (const brief of briefs) {
    const key = `${brief.kind}:${brief.scope_id}:${localDay(brief.generated_at)}`;
    const current = newest.get(key);
    if (current === undefined || current.generated_at < brief.generated_at) {
      newest.set(key, brief);
    }
  }
  return [...newest.values()].sort((a, b) => b.generated_at.localeCompare(a.generated_at));
}

function localDay(generatedAt: string): string {
  const at = new Date(generatedAt);
  return `${at.getFullYear()}-${at.getMonth() + 1}-${at.getDate()}`;
}
