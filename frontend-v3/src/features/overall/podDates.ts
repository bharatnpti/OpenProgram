// O6, "Dates by pod": each pod's own date as a line from today, in its verdict's
// colour, against the project's date; a pod with no date is a dashed line and a
// red chip. Also the release rows, drawn the same way. Pure; runtime imports by
// their .ts path, so `node --test` runs it as written.
import type { ScopeDeliveryResponse } from "../../api/schema";

import { counted, verdictChip } from "../../components/ui/dateStripWords.ts";
import { daysBetween, formatDay } from "../../lib/format.ts";
import { toneForVerdict, type BadgeTone } from "../../lib/status.ts";
import { undatedWords } from "./overallWords.ts";
import { changeKeys, shiftDay, type Phrase } from "./slip.ts";

type Scope = Pick<
  ScopeDeliveryResponse,
  | "scope_id"
  | "name"
  | "target"
  | "target_source"
  | "verdict"
  | "team"
  | "total"
  | "open"
  | "commitment"
  | "history"
>;

export type PodDateRow = {
  id: string;
  name: string;
  chip: { label: string; tone: BadgeTone };
  /** Where the pod's date sits along the track, 0–100; null for no date. */
  at: number | null;
  /** The line's colour: the pod's verdict, grey when there is none to go by. */
  tone: BadgeTone;
  /** One line under the track: the date against the project's, or why there is none. */
  fact: string;
  /** For the list's label: "Payments Pod Fri 20 Nov, at risk". */
  spoken: string;
};

export type PodDates = {
  rows: PodDateRow[];
  /** The project's date along the track, 0–100; null when it has none. */
  project: number | null;
  /** Month starts along the track, clear of Today and of the project's label. */
  ticks: { at: number; label: string }[];
  /** What the list says to a screen reader. */
  spoken: string;
};

/**
 * The tracks run from today to a little past the latest date shown: the
 * project's or any pod's. With no dates at all they run two months on, so the
 * dashed lines still read as a span of time.
 */
export function podDates(scopes: Scope[], projectTarget: string | null, today: string): PodDates {
  const dates = [projectTarget, ...scopes.map((scope) => scope.target)].filter(
    (day): day is string => Boolean(day) && (day as string) >= today,
  );
  const latest = dates.reduce((a, b) => (b > a ? b : a), shiftDay(today, 60));
  const span = Math.max(14, daysBetween(today, latest));
  const end = shiftDay(today, Math.round(span * 1.08));
  const total = Math.max(1, daysBetween(today, end));
  const at = (day: string) => Math.min(100, Math.max(0, (daysBetween(today, day) / total) * 100));
  const project = projectTarget ? at(projectTarget) : null;

  const rows = scopes.map((scope): PodDateRow => {
    const inScope = counted(scope);
    const chip = !inScope
      ? { label: "Nothing in scope", tone: "neutral" as const }
      : scope.verdict === "no_date"
        ? { label: "No committed date", tone: "danger" as const }
        : verdictChip(scope);
    return {
      id: scope.scope_id,
      name: scope.name,
      chip,
      at: scope.target ? at(scope.target) : null,
      tone: lineTone(scope),
      fact: podFact(scope, projectTarget, today),
      spoken: scope.target
        ? `${scope.name} ${formatDay(scope.target)}, ${chip.label.toLowerCase()}`
        : `${scope.name} has ${inScope ? "no committed date" : "nothing in scope"}`,
    };
  });

  const ticks: { at: number; label: string }[] = [];
  for (let month = firstOfNextMonth(today); month < end; month = firstOfNextMonth(month)) {
    const where = at(month);
    const clear = where > 12 && (project === null || Math.abs(where - project) > 12);
    if (clear) ticks.push({ at: where, label: formatDay(month).replace(/^\w+ /, "") });
  }

  return {
    rows,
    project,
    ticks,
    spoken: `${rows.length === 1 ? "The pod's date" : "Pod dates"} ${
      projectTarget
        ? `against the project date, ${formatDay(projectTarget)}`
        : "; the project has no committed date"
    }: ${rows.map((row) => row.spoken).join("; ")}`,
  };
}

/** The colour a scope's date is drawn in: its verdict's, grey when nothing is counted to judge. */
function lineTone(scope: Scope): BadgeTone {
  return counted(scope) ? toneForVerdict(scope.verdict) : "neutral";
}

/** Worst first, so a legend lists the colours the way the page ranks them. */
const TONE_ORDER: BadgeTone[] = ["danger", "warning", "success", "info", "neutral"];

export type LegendMarks = {
  /** The project's date is drawn, as a mark on every track. */
  project: boolean;
  /** Whose dates are drawn: only a pod or release that has a date draws one. */
  whose: ("pod" | "release")[];
  /** The colours those dates are in, worst first. */
  tones: BadgeTone[];
  /** Some row has no date, so its track is a dashed line. */
  noDate: boolean;
};

/**
 * What the "Dates by pod" legend may name: only the marks the tracks draw. The
 * project's mark needs a project date; a pod's or release's date needs a row
 * with one, and is shown in the colours those rows actually have; the dashed
 * line needs a row without one. A legend never lists a mark nothing draws, nor
 * a colour no date is in.
 */
export function datesLegend(
  pods: Scope[],
  releases: Scope[],
  projectTarget: string | null,
): LegendMarks {
  const dated = (scopes: Scope[]) => scopes.filter((scope) => Boolean(scope.target));
  const datedPods = dated(pods);
  const datedReleases = dated(releases);
  const tones = new Set([...datedPods, ...datedReleases].map(lineTone));
  return {
    project: Boolean(projectTarget),
    whose: [
      datedPods.length > 0 ? "pod" : null,
      datedReleases.length > 0 ? "release" : null,
    ].filter((kind): kind is "pod" | "release" => kind !== null),
    tones: TONE_ORDER.filter((tone) => tones.has(tone)),
    noDate: [...pods, ...releases].some((scope) => !scope.target),
  };
}

/**
 * The legend's words for the dates drawn, or null when none is: "A pod's date, in
 * its verdict's colour". Grey is named when a date has no verdict to go by.
 */
export function datesLegendWords(legend: Pick<LegendMarks, "whose" | "tones">): string | null {
  if (legend.whose.length === 0) return null;
  const whose = legend.whose.map((kind) => `${kind}'s`).join(" or ");
  const grey = legend.tones.includes("neutral");
  const coloured = legend.tones.some((tone) => tone !== "neutral");
  const colour = !grey
    ? "in its verdict's colour"
    : coloured
      ? "in its verdict's colour, grey with none"
      : "grey, with no verdict to colour it";
  return `A ${whose} date, ${colour}`;
}

/**
 * "Fri 20 Nov · 25 days before the project date"; without a date, why: the
 * team's latest date has gone by, or requirements carry none, or nothing is
 * counted for the pod.
 */
export function podFact(scope: Scope, projectTarget: string | null, today: string): string {
  if (scope.target) {
    const day = formatDay(scope.target);
    const past = scope.target < today ? ", past" : "";
    if (!projectTarget) return `${day}${past}`;
    const gap = daysBetween(scope.target, projectTarget);
    const days = `${Math.abs(gap)} ${Math.abs(gap) === 1 ? "day" : "days"}`;
    if (gap === 0) return `${day}${past} · on the project date`;
    return `${day}${past} · ${days} ${gap > 0 ? "before" : "after"} the project date`;
  }
  if (!counted(scope)) return "No requirements are counted for it yet.";
  const { latest, latest_key: key, undated } = scope.team;
  const keyed = key ? ` (${key})` : "";
  if (latest && latest < today)
    return `The team's latest date, ${formatDay(latest)}${keyed}, is past`;
  if (undated > 0) return capitalise(undatedWords(undated));
  if (latest) return `The team's latest date is ${formatDay(latest)}${keyed}`;
  return "No ETA or due date on its open requirements";
}

/**
 * The pods' dates as they were set and moved, newest first, each named by its
 * pod: "Payments Pod, set to Fri 20 Nov by Ira Novak on Wed 7 Oct: “…”".
 */
export function podChangeKeys(scopes: Pick<Scope, "name" | "commitment">[]): Phrase[] {
  return scopes
    .flatMap((scope) =>
      changeKeys(scope.commitment.changes).map((phrase, i) => ({
        at: scope.commitment.changes[i].changed_at,
        phrase: podPhrase(scope.name, phrase),
      })),
    )
    .sort((a, b) => b.at.localeCompare(a.at))
    .map((item) => item.phrase);
}

/** A change's key with its pod's name in bold at the front. */
function podPhrase(name: string, [before, bold, after]: Phrase): Phrase {
  if (before === "" && bold === "Cleared") return ["", name, `, cleared${after}`];
  if (before === "") return ["", name, `, set to ${bold}${after.replace(/^, set by/, " by")}`];
  return ["", name, `, ${before.toLowerCase()}${bold}${after}`];
}

function firstOfNextMonth(iso: string): string {
  const [year, month] = [Number(iso.slice(0, 4)), Number(iso.slice(5, 7))];
  const next =
    month === 12 ? `${year + 1}-01-01` : `${year}-${String(month + 1).padStart(2, "0")}-01`;
  return next;
}

function capitalise(text: string): string {
  return text ? `${text[0].toUpperCase()}${text.slice(1)}` : text;
}
