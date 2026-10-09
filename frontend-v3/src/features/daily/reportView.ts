// Pure helpers for the Daily view. Types, and one pure module imported by its .ts path,
// so `node --test` runs them.
import type { DayReportResponse, Rag, ReportRunResponse } from "../../api/schema";
import type { BadgeTone } from "../../lib/status";
import { withViewingDateParam } from "../../lib/viewingDate.ts";

/** The section whose groups are people, each with what is needed from them. */
export const ASKS_SECTION = "What we need, and from whom";

type RunStatus = ReportRunResponse["status"];

export const RUN_LABELS: Record<RunStatus, string> = {
  sent: "Sent",
  partial: "Partly sent",
  failed: "Not sent",
  sending: "Sending",
};

export const RUN_TONES: Record<RunStatus, BadgeTone> = {
  sent: "success",
  partial: "warning",
  failed: "danger",
  sending: "info",
};

/** The words the report itself opens with for each colour. */
export const RAG_WORDS: Record<Rag, string> = {
  green: "On track",
  amber: "At risk",
  red: "Off track",
  unknown: "Status unknown",
};

const ASK_KINDS = ["Fix", "Decision", "Answer", "Review"];

/**
 * An ask's kind, as the report starts each line under a person ("Fix: …"),
 * split off so it can be shown as a tag. Any other line keeps its text whole.
 */
export function askParts(line: string): { kind: string | null; text: string } {
  const at = line.indexOf(": ");
  const kind = at > 0 ? line.slice(0, at) : "";
  if (!ASK_KINDS.includes(kind)) return { kind: null, text: line };
  return { kind, text: line.slice(at + 2) };
}

/** What the confirm step before "Send now" says, in the server's own words for where. */
export function sendConfirmation(
  report: Pick<DayReportResponse, "name" | "destination_count" | "audience_summary">,
): { title: string; description: string } {
  const count = report.destination_count;
  const receive = count === 1 ? "1 destination receives" : `${count} destinations receive`;
  return {
    title: `Send ${report.name} now?`,
    description:
      `${receive} it right away: ${report.audience_summary}. ` +
      "Today's scheduled send still goes out at its time.",
  };
}

/**
 * Where the footer's "Open in OpenProgram" goes inside the console: the path the
 * server gave (`console_path`, the report's own page), as a link within this
 * app, keeping the past day being viewed. The address a sent message carries
 * is absolute because it is opened elsewhere; here an address with a host would
 * leave the console, which is how this link broke, so only a path is taken.
 * Null when there is none to follow.
 */
export function openInConsoleTarget(
  consolePath: string | null | undefined,
  asOf: string | null,
): string | null {
  if (!consolePath || !consolePath.startsWith("/")) return null;
  if (consolePath.startsWith("//") || consolePath.startsWith("/\\")) return null;
  const hashAt = consolePath.indexOf("#");
  const hash = hashAt < 0 ? "" : consolePath.slice(hashAt);
  const beforeHash = hashAt < 0 ? consolePath : consolePath.slice(0, hashAt);
  const queryAt = beforeHash.indexOf("?");
  const pathname = queryAt < 0 ? beforeHash : beforeHash.slice(0, queryAt);
  const search = queryAt < 0 ? "" : beforeHash.slice(queryAt);
  return `${pathname}${withViewingDateParam(search, asOf)}${hash}`;
}

/** "3 of 4 delivered" for a run's outcomes. */
export function outcomeLine(run: Pick<ReportRunResponse, "outcomes">): string {
  const total = run.outcomes.length;
  if (total === 0) return "no destinations";
  const ok = run.outcomes.filter((o) => o.ok).length;
  return `${ok} of ${total} delivered`;
}

/** Today's note text when the stored note is for `today`, else empty. */
export function todaysNote(report: Pick<DayReportResponse, "note">, today: string): string {
  return report.note && report.note.report_date === today ? report.note.text : "";
}

/**
 * The report's own today (YYYY-MM-DD) in its time zone: the day the server
 * files today's note and today's send under, not the viewer's UTC date.
 */
export function localDay(timezone: string, now: Date = new Date()): string {
  try {
    return new Intl.DateTimeFormat("en-CA", {
      timeZone: timezone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(now);
  } catch {
    return now.toISOString().slice(0, 10);
  }
}

/** "17:30" for the schedule's local time, which the server keeps with seconds. */
export function scheduleTime(localTime: string): string {
  return /^\d{2}:\d{2}/.test(localTime) ? localTime.slice(0, 5) : localTime;
}

/**
 * One destination of a send, as a line: who or where, and what happened. A
 * failure says so first, so it is not read as delivered by its detail alone.
 */
export function outcomeText(outcome: { label: string; ok: boolean; detail: string }): string {
  const detail = outcome.detail.trim();
  if (outcome.ok) return detail ? `${outcome.label}: ${detail}` : `${outcome.label}: delivered`;
  return detail ? `${outcome.label}: not delivered. ${detail}` : `${outcome.label}: not delivered`;
}

/**
 * How many day reports a project has, in words, for the Reports list. `count`
 * is undefined until the reports are read: "No day report set up yet" is a
 * finding, so it is not said while they load or when they could not be read.
 */
export function dayReportCountWords(count: number | undefined, failed: boolean): string {
  if (failed) return "Day reports could not be read";
  if (count === undefined) return "Loading day reports…";
  if (count === 0) return "No day report set up yet";
  return count === 1 ? "1 day report" : `${count} day reports`;
}

/**
 * What an empty Daily page says after "No day report is set up for this
 * project yet.": an offer to set one up, to someone who may here. That depends
 * on where they may set reports up (`setup`), a second read: until it answers
 * the page does not offer and take it back. Nobody else is told who does.
 */
export function noDayReportWords(
  canSetUp: boolean,
  setup: "loading" | "failed" | "ready",
  mayHere: boolean,
): string {
  return canSetUp && setup === "ready" && mayHere
    ? "Set one up to send it at the end of each day."
    : "";
}
