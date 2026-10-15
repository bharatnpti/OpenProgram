// What the hub's tab bar and pod bar show: the tabs a role has at a place,
// where each tab, level and pod pick goes, and each tab's badge in words. The
// badges carry each tab's summary, so no Overview tab is needed. Pure, with
// type imports and runtime imports by their .ts path, so `node --test` runs it.
import type {
  DayReportResponse,
  PortfolioPodDto,
  ScopeProjectDto,
  Verdict,
} from "../../api/schema";
import { VERDICT_LABELS, toneForVerdict, type BadgeTone } from "../../lib/status.ts";
import {
  carriedQuery,
  placePath,
  type HubLevel,
  type HubPlace,
  type HubSpot,
  type HubTab,
} from "./hubPaths.ts";

/** The tabs a role has at each level (app/access.ts `hub.tabs`). */
export type TabsByLevel = Record<HubLevel, readonly HubTab[]>;

/** `tab` when the level has it, else the level's first tab; null for a level with none. */
export function tabAt(tabs: TabsByLevel, level: HubLevel, tab: HubTab | null): HubTab | null {
  const offered = tabs[level];
  if (tab && offered.includes(tab)) return tab;
  return offered[0] ?? null;
}

/**
 * Where a move in the hub goes: the place asked for at the same tab when the
 * level has it (else its first), with the query that travels (`carriedQuery`:
 * the viewing day, the release while the project stays, the programme between
 * the All level's tabs). Picking a pod keeps the tab; picking a tab keeps the pod.
 */
export function hubHref(
  tabs: TabsByLevel,
  from: HubSpot,
  place: HubPlace,
  tab: HubTab | null = from.tab,
  search = "",
): string {
  const next = tabAt(tabs, place.level, tab);
  return `${placePath(place, next)}${carriedQuery(search, from, { place, tab: next })}`;
}

/** A tab's badge: what it shows and what a screen reader says for it. */
export type Badge = { text: string; label: string };

/** Early warnings: how many are open, "8"; none for none. */
export function warningsBadge(total: number | null | undefined): Badge | null {
  if (!total || total <= 0) return null;
  return { text: String(total), label: `${total} open ${total === 1 ? "warning" : "warnings"}` };
}

/**
 * Scope control: what joined after planning and the open Jira hygiene
 * findings, "+2 · 3"; either alone when the other is none, nothing for neither.
 */
export function scopeBadge(
  added: number | null | undefined,
  hygieneOpen: number | null | undefined,
): Badge | null {
  const joined = added && added > 0 ? added : 0;
  const open = hygieneOpen && hygieneOpen > 0 ? hygieneOpen : 0;
  if (joined === 0 && open === 0) return null;
  const text = [joined ? `+${joined}` : null, open ? String(open) : null]
    .filter(Boolean)
    .join(" · ");
  const label = [
    joined ? `${joined} added after planning` : null,
    open ? `${open} open Jira hygiene ${open === 1 ? "finding" : "findings"}` : null,
  ]
    .filter(Boolean)
    .join(", ");
  return { text, label };
}

/**
 * What a place's badges are read over: the whole portfolio at All projects,
 * one project, or one pod's part of one project. The warnings and scope
 * badges are each one bulk read narrowed to it, the read its tab draws.
 */
export function badgeScope(place: HubPlace): { projectId: string | null; podId: string | null } {
  if (place.level === "all") return { projectId: null, podId: null };
  return { projectId: place.projectId, podId: place.level === "pod" ? place.podId : null };
}

/**
 * The Scope control badge at a place from the scope read: across every
 * project at All projects (what joined after planning, and the open findings),
 * else the place's project row, which the read narrows to the pod at a pod.
 */
export function scopeBadgeAt(
  projectId: string | null,
  read: { projects: readonly ScopeProjectDto[]; findings_total: number } | undefined,
): Badge | null {
  if (!read) return null;
  if (projectId === null) {
    return scopeBadge(
      read.projects.reduce((sum, project) => sum + (project.plan?.added ?? 0), 0),
      read.findings_total,
    );
  }
  return projectScopeBadge(read.projects.find((project) => project.id === projectId));
}

/** A project's row of the scope read, as its badge. */
export function projectScopeBadge(project: ScopeProjectDto | undefined): Badge | null {
  return project ? scopeBadge(project.plan?.added ?? 0, project.hygiene_open) : null;
}

/**
 * Reports: when today's day report went out, "Sent 17:30" (the latest of the
 * project's reports sent on `day`), or "Not set up" when the project has
 * none. Nothing while the reports are read, and nothing when one is set up but
 * none went out that day.
 */
export function reportsBadge(
  reports: readonly DayReportResponse[] | undefined,
  day: string,
  timeOf: (iso: string) => string,
): Badge | null {
  if (!reports) return null;
  if (reports.length === 0) return { text: "Not set up", label: "No day report is set up" };
  const sent = reports
    .map((report) => report.last_run)
    .filter(
      (run): run is NonNullable<typeof run> =>
        Boolean(run) &&
        run?.report_date === day &&
        (run?.status === "sent" || run?.status === "partial"),
    )
    .map((run) => run.finished_at ?? run.started_at)
    .sort();
  const last = sent[sent.length - 1];
  if (!last) return null;
  const time = timeOf(last);
  return { text: `Sent ${time}`, label: `Day report sent at ${time}` };
}

/** One pod in the pod bar: its date verdict where the reader has it. */
export type PodChoice = {
  id: string;
  name: string;
  verdict: Verdict | null;
  /** Requirements counted for it on this project; zero is nothing in scope. */
  total: number | null;
  /** One of the person's own pods (they belong to it or run it). */
  own: boolean;
};

/** A pod's verdict as its chip: the date's words and tone; null where it has none. */
export function podVerdict(pod: Pick<PodChoice, "verdict" | "total">): {
  label: string;
  tone: BadgeTone;
} | null {
  if (pod.total !== null && pod.total <= 0) return { label: "Nothing in scope", tone: "neutral" };
  if (!pod.verdict) return null;
  // A date nobody committed to is not on track: red, as its tile is.
  const tone = pod.verdict === "no_date" ? "danger" : toneForVerdict(pod.verdict);
  return { label: VERDICT_LABELS[pod.verdict], tone };
}

const VERDICT_WEIGHT: Record<Verdict, number> = {
  off_track: 5,
  no_date: 4,
  at_risk: 3,
  not_enough_data: 2,
  on_track: 1,
  done: 0,
};

/**
 * The pods of a project, as the pod bar lists them: the ones the bulk read
 * gives this project (with their verdict), then, unless the reader is held to
 * those (`rowsOnly`, a counts-only reader, whose pods under five the server
 * leaves out), the project's other pods from the directory, with none. Worst
 * first, then by name; a pod listed twice keeps its verdict.
 */
export function podChoices(
  rows: readonly PortfolioPodDto[] | null,
  directory: readonly { id: string; name: string }[],
  own: ReadonlySet<string>,
  rowsOnly: boolean,
): PodChoice[] {
  const listed: PodChoice[] = (rows ?? []).map((pod) => ({
    id: pod.id,
    name: pod.name,
    verdict: pod.verdict ?? null,
    total: pod.total,
    own: own.has(pod.id),
  }));
  const seen = new Set(listed.map((pod) => pod.id));
  const rest: PodChoice[] = rowsOnly
    ? []
    : directory
        .filter((pod) => !seen.has(pod.id))
        .map((pod) => ({
          id: pod.id,
          name: pod.name,
          verdict: null,
          total: null,
          own: own.has(pod.id),
        }));
  const weight = (pod: PodChoice) => (pod.verdict ? VERDICT_WEIGHT[pod.verdict] : -1);
  return [...listed, ...rest].sort((a, b) => weight(b) - weight(a) || a.name.localeCompare(b.name));
}

/**
 * Whether a pod's own level opens for a reader: always, unless the reader needs
 * `minPeople` (a counts-only reader, five), when only a pod the bulk read lists
 * for them and of that many people or more opens. A pod of unknown size stays
 * closed, as does one the read leaves out: the server refuses both its own reads.
 */
export function opensPodLevel(
  minPeople: number,
  pod: { listed: boolean; people: number | null },
): boolean {
  if (minPeople <= 0) return true;
  return pod.listed && pod.people !== null && pod.people >= minPeople;
}

/** A date verdict's tone: a date nobody committed to is red, as its tile is. */
export function verdictTone(verdict: Verdict | null): BadgeTone {
  if (verdict === "on_track" || verdict === "done") return "success";
  if (verdict === "at_risk") return "warning";
  if (verdict === "off_track" || verdict === "no_date") return "danger";
  return "neutral";
}

/** The verdict in words, for a screen reader and a tooltip; "No forecast yet" for none. */
export function verdictWords(verdict: Verdict | null): string {
  return verdict ? VERDICT_LABELS[verdict] : "No forecast yet";
}

/** How many pods the bar shows as chips before the rest go into one "More pods" pick. */
export const PODS_AS_CHIPS = 4;
