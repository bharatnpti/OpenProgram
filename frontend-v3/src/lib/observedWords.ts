// What the tracker says beside what people said, in words. Pure, and only type
// imports plus modules by their .ts path, so `node --test` runs it as written.
//
// The backend reads it from Jira's history (core/domain/observed_day.py): a
// task's status since when and who moved it, the day's other changes, and how
// a pod's day was captured. None of it is anyone's word: the "Jira today" line
// sits under "Last said" and is never merged into it.
import type { CheckinCoverageDto, NoDataCause, ObservedTaskDto } from "../api/schema";
import { formatDay, formatTime } from "./format.ts";

/** Why a member's day has no data, as the coverage line says it. */
export const NO_DATA_WORDS: Record<NoDataCause, string> = {
  no_jira_link: "no Jira link",
  jira_not_synced: "Jira not synced",
  no_chat_id: "no chat id",
  nothing_moved: "nothing moved in Jira",
};

type Say = {
  /** "Tue 6 Oct" */
  day?: (iso: string) => string;
  /** "14:02" */
  time?: (iso: string) => string;
  /** The day the screen is for, "2026-10-06": a move that day reads by its time. */
  today: string;
  /** The viewer's own calendar day of an instant; the viewer's local day by default. */
  localDay?: (iso: string) => string;
};

function viewerDay(iso: string): string {
  const at = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}`;
}

/**
 * "Jira today: In Review since 14:02 · moved by Liam · due date moved Oct 10 → Oct 14",
 * "Jira today: In Progress since Mon 5 Oct · no movement for 8 days", or null when
 * the tracker's history says nothing about the task.
 */
export function jiraTodayLine(
  observed: ObservedTaskDto | null | undefined,
  say: Say,
): string | null {
  if (!observed) return null;
  const day = say.day ?? formatDay;
  const time = say.time ?? formatTime;
  const localDay = say.localDay ?? viewerDay;
  const parts: string[] = [];
  if (observed.status) {
    if (observed.since) {
      const when =
        localDay(observed.since) === say.today ? time(observed.since) : day(observed.since);
      parts.push(`${observed.status} since ${when}`);
    } else {
      parts.push(observed.status);
    }
  }
  if (observed.moved_by) parts.push(`moved by ${observed.moved_by}`);
  parts.push(...(observed.changes ?? []));
  if (observed.quiet_days) parts.push(`no movement for ${observed.quiet_days} days`);
  return parts.length > 0 ? `Jira today: ${parts.join(" · ")}` : null;
}

/**
 * The coverage parts after "4 of 6 replied today": " · 3 from Jira · 1 no data
 * (no Jira link)". One cause is named on its own; several are counted:
 * "2 no data (1 no Jira link, 1 nothing moved in Jira)". Empty without coverage.
 */
export function coverageWords(coverage: CheckinCoverageDto | null | undefined): string {
  if (!coverage) return "";
  const parts: string[] = [];
  if (coverage.inferred > 0) parts.push(`${coverage.inferred} from Jira`);
  if (coverage.no_data > 0) {
    const causes = coverage.causes ?? [];
    const why =
      causes.length === 1
        ? NO_DATA_WORDS[causes[0].cause]
        : causes.map((item) => `${item.count} ${NO_DATA_WORDS[item.cause]}`).join(", ");
    parts.push(`${coverage.no_data} no data${why ? ` (${why})` : ""}`);
  }
  return parts.map((part) => ` · ${part}`).join("");
}
