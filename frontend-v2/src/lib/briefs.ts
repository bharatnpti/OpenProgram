import type { BriefKind, NarrativeBriefResponse } from "../api/schema";

/** Every brief kind, widest scope first: the order the Coordination filter offers. */
export const BRIEF_KINDS: readonly BriefKind[] = ["exec", "weekly_project", "daily_pod"];

export const BRIEF_KIND_LABEL: Record<BriefKind, string> = {
  exec: "Exec",
  weekly_project: "Weekly project",
  daily_pod: "Daily pod",
};

/** The Coordination search param that holds the briefs filter, so Today can link to one kind. */
const BRIEF_KIND_PARAM = "briefs";

/** The brief kind the params name, or undefined for "all" and anything unrecognised. */
export function briefKindParam(params: URLSearchParams): BriefKind | undefined {
  const value = params.get(BRIEF_KIND_PARAM);
  return BRIEF_KINDS.find((kind) => kind === value);
}

/** The same params with the briefs filter set to `kind`, or cleared for "all". */
export function withBriefKindParam(
  params: URLSearchParams,
  kind: BriefKind | undefined,
): URLSearchParams {
  const next = new URLSearchParams(params);
  if (kind === undefined) {
    next.delete(BRIEF_KIND_PARAM);
  } else {
    next.set(BRIEF_KIND_PARAM, kind);
  }
  return next;
}

/** Coordination with its briefs column filtered to one kind. */
export function briefsHref(kind: BriefKind): string {
  return `/coordination?${withBriefKindParam(new URLSearchParams(), kind).toString()}`;
}

/** The newest brief for each scope on each day it was generated.
 *
 * A seeded brief and the worker's own cron run both land on the same day, so
 * the same pod or project appeared twice with slightly different wording.
 * Keeping the newest per scope per day preserves the day-by-day record that
 * makes the column read like a real log, without the duplicate.
 *
 * The exec brief has one scope, the whole portfolio, but not one scope id: the
 * seed writes it against the program and the worker against "". Keyed by scope
 * id, both would show for the same day, so it is keyed by kind alone.
 *
 * Days are the *local* days the UI prints, so grouping can never disagree with
 * the dates on screen.
 */
export function latestBriefPerScopePerDay(
  briefs: readonly NarrativeBriefResponse[],
): NarrativeBriefResponse[] {
  const newest = new Map<string, NarrativeBriefResponse>();
  for (const brief of briefs) {
    const scope = brief.kind === "exec" ? "" : brief.scope_id;
    const key = `${brief.kind}:${scope}:${localDay(brief.generated_at)}`;
    const current = newest.get(key);
    if (current === undefined || current.generated_at < brief.generated_at) {
      newest.set(key, brief);
    }
  }
  return [...newest.values()].sort((a, b) => b.generated_at.localeCompare(a.generated_at));
}

/** The local day a brief was generated, as YYYY-MM-DD, matching the dates the UI prints. */
export function localDay(generatedAt: string): string {
  const at = new Date(generatedAt);
  const month = String(at.getMonth() + 1).padStart(2, "0");
  const day = String(at.getDate()).padStart(2, "0");
  return `${at.getFullYear()}-${month}-${day}`;
}
