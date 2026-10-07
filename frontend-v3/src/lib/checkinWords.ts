// Pure wording for how a person's check-in stands. Type imports only, so
// `node --test` runs it directly.
import type { PodCheckinsResponse, StatusSource } from "../api/schema";
import type { BadgeTone } from "./status";

/*
 * One word per meaning, on every screen that says how someone's check-in stands
 * (Today, Delivery, Signals):
 *
 *   replied                    the person gave today's status themselves, in chat
 *                              or by confirming it in the console
 *   confirmed                  ... and confirmed or corrected it in the console:
 *                              only what the person did, and said only where the
 *                              API says it (their own status carries
 *                              `developer_confirmed`; a pod's board does not)
 *   partly replied             replied, but left blockers or the ETA open
 *   replied without a status   replied, but said nothing about their work, so the
 *                              status stays unknown
 *   no reply                   asked, and nothing came back
 *   inferred                   no reply; the status is worked out from Jira and Git
 *   carried forward            no reply today; an earlier day's status stands in
 *
 * "Confirmed" used to mean two things. The pod board called anyone whose reply
 * was recorded "confirmed" (the backend's StatusSource.CONFIRMED is the
 * person's own word, whoever pressed what), while the person's own card said
 * "answered in chat" and offered Confirm: Zoe was confirmed to her scrum master
 * and not to herself. A board cannot tell a chat reply from a confirmation, so
 * it says "replied", which is true of both.
 */

export const REPLY_WORDS = {
  replied: "replied",
  partly: "partly replied",
  withoutStatus: "replied without a status",
  confirmed: "confirmed",
  none: "no reply",
  inferred: "inferred",
  carried: "carried forward",
} as const;

/**
 * The summary the backend writes when someone answered the day's check-in
 * without saying anything about their work (status_summaries
 * `NON_STATUS_REPLY_SUMMARY`: "Replied without a status update. Current status
 * is unknown."). The status itself is `unknown`, the same as for someone never
 * asked, so this sentence is the only mark of the case the API carries.
 */
const NON_STATUS_LEAD = "Replied without a status update";

/** Whether a status says its person replied without a status (see `NON_STATUS_LEAD`). */
export function repliedWithoutStatus(
  summary: string | null | undefined,
  source?: StatusSource | null,
): boolean {
  if (source !== undefined && source !== null && source !== "unknown") return false;
  return (summary ?? "").trim().startsWith(NON_STATUS_LEAD);
}

type BoardPerson = Pick<
  PodCheckinsResponse["developers"][number],
  "state" | "source" | "status_as_of" | "summary"
>;

/** What a pod's board knows of a person: its server state, source, day and summary. */
export type BoardRead = BoardPerson;

/**
 * The word for a person on a board of check-ins, and how it is toned. `asOf` is
 * the day the board is for; a status from another day is carried forward.
 */
export function boardWord(person: BoardPerson, asOf: string): { word: string; tone: BadgeTone } {
  if (person.state === "confirmed") return { word: REPLY_WORDS.replied, tone: "success" };
  if (person.state === "partial") return { word: REPLY_WORDS.partly, tone: "warning" };
  if (repliedWithoutStatus(person.summary, person.source)) {
    return { word: REPLY_WORDS.withoutStatus, tone: "warning" };
  }
  if (person.state === "missing") return { word: REPLY_WORDS.none, tone: "neutral" };
  if (person.source === "inferred") return { word: REPLY_WORDS.inferred, tone: "warning" };
  if (person.status_as_of && person.status_as_of !== asOf) {
    return { word: REPLY_WORDS.carried, tone: "warning" };
  }
  return { word: REPLY_WORDS.none, tone: "warning" };
}

/** The colour of a person's row on a board: replied green, silence grey, the rest amber. */
export function boardRag(person: BoardPerson): "green" | "amber" | "unknown" {
  if (person.state === "confirmed") return "green";
  if (repliedWithoutStatus(person.summary, person.source)) return "amber";
  return person.state === "missing" ? "unknown" : "amber";
}

/**
 * The line under a person on a board: when they replied, or what stands in for
 * a reply, then their summary. A status from an earlier day names it, and what
 * it was then, so "replied" is never read as "replied today".
 */
export function boardMeta(
  person: BoardPerson,
  asOf: string,
  say: { day: (iso: string) => string },
): string {
  const { state, source, status_as_of: from, summary } = person;
  if (repliedWithoutStatus(summary, source)) return "replied without a status";
  if (state === "missing") return "no status yet";
  let lead: string;
  if (from && from !== asOf) {
    const day = say.day(from);
    lead =
      source === "confirmed"
        ? `last replied ${day}, nothing today`
        : source === "partial"
          ? `last partly replied ${day}, nothing today`
          : source === "inferred"
            ? `inferred ${day}, nothing today`
            : `no reply since ${day}`;
  } else if (state === "confirmed") {
    lead = "replied today";
  } else if (state === "partial") {
    lead = "partly replied today";
  } else {
    lead = source === "inferred" ? "no reply · inferred from delivery signals" : "no reply today";
  }
  return summary ? `${lead} · ${summary}` : lead;
}

/** "5 of 6 replied", with those who replied in part beside it: "5 of 6 replied · 1 partly". */
export function repliedCount(counts: { confirmed: number; partial: number; total: number }) {
  const partly = counts.partial > 0 ? ` · ${counts.partial} partly` : "";
  return `${counts.confirmed} of ${counts.total} replied${partly}`;
}

/**
 * A person's status source, as their check-in: where Signals says what an owner
 * has said. Not for a task or a blocker, whose source says where its own status
 * came from.
 */
export function ownerSourceWords(source: StatusSource | null | undefined): string {
  switch (source) {
    case "confirmed":
      return REPLY_WORDS.replied;
    case "partial":
      return REPLY_WORDS.partly;
    case "inferred":
      return REPLY_WORDS.inferred;
    case "stale":
      return REPLY_WORDS.carried;
    default:
      return "no status";
  }
}

/** What an owner stated about the work, for a drift finding: "stated in a reply". */
export function statedSourceWords(source: StatusSource | null | undefined): string {
  switch (source) {
    case "confirmed":
      return "stated in a reply";
    case "partial":
      return "stated in a partial reply";
    case "inferred":
      return "inferred, not stated";
    case "stale":
      return "stated earlier, carried forward";
    default:
      return "no status stated";
  }
}
