// What a DateStrip says, worked out from a scope's delivery read: the committed
// date and who set it, the forecast and its gap to that date (or why there is
// none), the team's own latest date, and the verdict. Type imports and pure
// modules by their .ts path, so `node --test` runs it as written.
import type { Rag, ScopeDeliveryResponse, Verdict } from "../../api/schema";
import { daysBetween, formatDay } from "../../lib/format.ts";
import { VERDICT_LABELS, toneForVerdict, type BadgeTone } from "../../lib/status.ts";

type Scope = Pick<
  ScopeDeliveryResponse,
  "target" | "target_source" | "commitment" | "history" | "team" | "verdict" | "total"
>;

/** A scope with no requirements counted has nothing to forecast and no verdict worth showing. */
export function counted(scope: Pick<ScopeDeliveryResponse, "total">): boolean {
  return scope.total > 0;
}

/** The colour of the strip's edge: the verdict's, grey when there is none to go by. */
export function verdictRag(verdict: Verdict, inScope: boolean): Rag {
  if (!inScope) return "unknown";
  if (verdict === "off_track") return "red";
  if (verdict === "at_risk") return "amber";
  if (verdict === "on_track" || verdict === "done") return "green";
  return "unknown";
}

/** The verdict chip: its words and tone, "Nothing in scope" when nothing is counted. */
export function verdictChip(scope: Scope): { label: string; tone: BadgeTone } {
  if (!counted(scope)) return { label: "Nothing in scope", tone: "neutral" };
  return { label: VERDICT_LABELS[scope.verdict], tone: toneForVerdict(scope.verdict) };
}

/** Worst first: off track, at risk, then the ones that cannot be judged, then on track and done. */
export function verdictWeight(scope: Scope): number {
  if (!counted(scope)) return 1;
  switch (scope.verdict) {
    case "off_track":
      return 5;
    case "at_risk":
      return 4;
    case "not_enough_data":
      return 3;
    case "no_date":
      return 2;
    case "on_track":
      return 0;
    default:
      return -1;
  }
}

export type Gap = { tone: BadgeTone; text: string };

/**
 * The forecast against the committed date: "+12 days" in danger when the 50%
 * date is later, a warning when only the 85% date is, "on time" otherwise.
 * Nothing without a committed date or a forecast.
 */
export function forecastGap(
  target: string | null,
  p50: string | null,
  p85: string | null,
): Gap | null {
  if (!target || !p50) return null;
  const late = daysBetween(target, p50);
  if (late > 0) return { tone: "danger", text: `+${late} ${late === 1 ? "day" : "days"}` };
  const tail = p85 ? daysBetween(target, p85) : 0;
  if (tail > 0) return { tone: "warning", text: `85%: +${tail} ${tail === 1 ? "day" : "days"}` };
  return { tone: "success", text: "on time" };
}

/**
 * How much history a forecast has against what it needs, from the server's own
 * reason ("Only 2 working days of history; a forecast needs 10.") when it says,
 * else the days it has.
 */
export function historyWords(history: Scope["history"]): string {
  const said = /(\d+) working days? of history; a forecast needs (\d+)/.exec(history.reason ?? "");
  if (said) return `${said[1]} of ${said[2]} working days`;
  const days = history.sample_days;
  return `${days} working ${days === 1 ? "day" : "days"} of history`;
}

/**
 * Under the committed date: who committed it and when, how often it moved, or
 * that it comes from the Jira release. "by Mina Patel · Wed 7 Oct · moved once, +7 days".
 */
export function committedBy(scope: Scope): string | null {
  if (!scope.target) return null;
  if (scope.target_source === "jira_release") return "from the Jira release";
  const changes = scope.commitment.changes;
  const last = changes[changes.length - 1];
  const parts = last ? [`by ${last.changed_by_name}`, formatDay(last.changed_at)] : [];
  const moved = scope.commitment.times_moved;
  if (moved > 0) {
    const days = scope.commitment.moved_days;
    const times = moved === 1 ? "moved once" : `moved ${moved} times`;
    parts.push(days ? `${times}, ${days > 0 ? "+" : ""}${days} days` : times);
  }
  return parts.length > 0 ? parts.join(" · ") : null;
}

/** Under the team's date: the item it comes from, and how many open ones have none. "CHK-4 · 6 without a date". */
export function teamWords(team: Scope["team"]): string {
  const parts = [
    team.latest_key,
    team.undated > 0 ? `${team.undated} without a date` : null,
  ].filter((part): part is string => Boolean(part));
  return parts.join(" · ") || `latest of ${team.dated} dated`;
}

const DONE_STATUS = /^(done|closed|resolved|released|completed?|in production)$/i;

/**
 * Whether a task is past its due date on the day shown: due before that day,
 * and not done in the tracker. A calendar day compares as itself, in every zone.
 */
export function pastDue(
  deadline: string | null | undefined,
  shownDay: string | null | undefined,
  trackerStatus?: string | null,
): boolean {
  if (!deadline || !shownDay) return false;
  if (trackerStatus && DONE_STATUS.test(trackerStatus.trim())) return false;
  return deadline.slice(0, 10) < shownDay.slice(0, 10);
}

/**
 * The compact strip's three parts, each said once: the date (null when none is
 * committed), the forecast in a few words, and the verdict chip. A verdict that
 * says what the line already says (no committed date, not enough history) does
 * not say it twice: the chip goes, or the forecast words do.
 */
export function compactParts(scope: Scope): {
  date: string | null;
  forecast: string | null;
  chip: { label: string; tone: BadgeTone } | null;
} {
  const chip = verdictChip(scope);
  const inScope = counted(scope);
  if (inScope && scope.verdict === "no_date") {
    return { date: null, forecast: compactForecast(scope), chip: null };
  }
  if (inScope && scope.verdict === "not_enough_data") {
    const team = scope.team.latest ? `team says ${formatDay(scope.team.latest)}` : null;
    return { date: scope.target, forecast: team, chip };
  }
  return { date: scope.target, forecast: compactForecast(scope), chip };
}

/** The forecast in a few words for the compact strip. */
export function compactForecast(scope: Scope): string {
  if (!counted(scope)) return "nothing to forecast";
  const { p50, p85 } = scope.history;
  if (!p50) {
    // With no forecast yet, the team's own latest date is the date there is.
    return scope.team.latest
      ? `not enough history · team says ${formatDay(scope.team.latest)}`
      : "forecast: not enough history";
  }
  const gap = forecastGap(scope.target, p50, p85);
  return `forecast: ${formatDay(p50)}${gap ? `, ${gap.text}` : ""}`;
}
