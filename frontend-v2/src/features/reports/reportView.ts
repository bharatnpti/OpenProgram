// Only type imports here: node --test strips types but cannot resolve
// extensionless runtime imports.
import type {
  DayReportResponse,
  Rag,
  ReportAudienceResponse,
  ReportRunResponse,
} from "../../api/schema";
import type { BadgeTone } from "../../lib/status";

type RunStatus = ReportRunResponse["status"];

/** Every day-report query starts with "day-reports", so one invalidation refreshes them all. */
export function reportRunsQueryKey(reportId: string) {
  return ["day-reports", "runs", reportId] as const;
}

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

/**
 * What the confirm dialog before "Send now" says: how many destinations get the
 * report, and where, in the server's own words.
 */
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
 * The people a report goes to by name, as the server named them: "Asha Rao,
 * Ira Novak and Mina Patel", with anyone it could not name counted
 * ("Asha Rao and 1 more"). Null when it goes to no person.
 */
export function peopleLine(audience: ReportAudienceResponse[]): string | null {
  const people = audience.find((item) => item.kind === "person");
  if (!people || people.count === 0) return null;
  const names = [...people.names];
  const unnamed = people.count - names.length;
  if (unnamed > 0) names.push(`${unnamed} more`);
  if (people.names.length === 0) return null;
  if (names.length === 1) return names[0];
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

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

/** The progress bar's filled share, 0 to 100; nothing known is an empty bar. */
export function progressWidth(percent: number | null | undefined): number {
  if (percent === null || percent === undefined || Number.isNaN(percent)) return 0;
  return Math.max(0, Math.min(100, percent));
}

/** "Tue 6 Oct" for a report's ISO day, read at midday so no timezone moves it. */
export function formatReportDay(iso: string): string {
  return new Date(`${iso}T12:00:00`).toLocaleDateString("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
  });
}
