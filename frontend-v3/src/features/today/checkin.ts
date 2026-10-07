// Only type imports here: this module runs under `node --test` as written,
// which strips types but resolves no extensionless runtime import.
import type {
  BlockerDetailDto,
  MyStatusResponse,
  StatusCorrectionRequest,
  StatusSource,
} from "../../api/schema";
import { REPLY_WORDS, boardWord, repliedWithoutStatus } from "../../lib/checkinWords.ts";

/**
 * The placeholder blocker a check-in nobody answered carries when nothing is
 * open (backend `NO_REPLY_BLOCKER`). It is not a blocker the person has, so a
 * correction must never send it back as one.
 */
export const NO_REPLY_BLOCKER = "no confirmed reply";

/** Whether a blocker's text is the placeholder, and so not a blocker anyone has. */
export function isNoReplyPlaceholder(description: string): boolean {
  return blockerKey(description) === NO_REPLY_BLOCKER;
}

/*
 * Where a check-in came from.
 *
 * The backend answers /me/status with the latest status at or before the day
 * asked for, so on a day with no reply the card holds an EARLIER day's status
 * carried forward. It marks that by `status_as_of` (the day the status is
 * from) differing from the day asked for (`as_of` on /me/focus). Only a
 * confirmed or partial status dated that day counts as answered; anything else
 * is "stale" to the pod board (persona_views `_checkin_state`), and this
 * mirrors it so the developer and their scrum master read one answer.
 */
export type CheckinState = "confirmed" | "partial" | "stale" | "missing";

export function checkinState(
  source: StatusSource | undefined,
  statusAsOf: string | null | undefined,
  today: string,
): CheckinState {
  if (!statusAsOf || source === undefined || source === "unknown") return "missing";
  if (statusAsOf === today && source === "confirmed") return "confirmed";
  if (statusAsOf === today && source === "partial") return "partial";
  return "stale";
}

export type CheckinProvenance = {
  /** `today`: dated the day viewed. `carried`: an earlier day's status stands in. `none`: nothing on record. */
  kind: "today" | "carried" | "none";
  /** The earlier day the status is carried forward from; null unless `carried`. */
  from: string | null;
  /** Said by the person here today: the green tick. */
  confirmedToday: boolean;
  state: CheckinState;
};

export function checkinProvenance(input: {
  source: StatusSource | undefined;
  statusAsOf: string | null | undefined;
  today: string;
  developerConfirmed: boolean;
}): CheckinProvenance {
  const state = checkinState(input.source, input.statusAsOf, input.today);
  if (!input.statusAsOf) return { kind: "none", from: null, confirmedToday: false, state };
  if (input.statusAsOf !== input.today) {
    return { kind: "carried", from: input.statusAsOf, confirmedToday: false, state };
  }
  return {
    kind: "today",
    from: null,
    confirmedToday: input.source === "confirmed" && input.developerConfirmed,
    state,
  };
}

/**
 * The line in the card's corner: what the status is and when it is from, in the
 * words every screen uses for a check-in (see lib/checkinWords.ts). A chat reply
 * the person has not confirmed in the console says so: "replied in chat, not
 * confirmed", where it used to say "answered in chat" beside a pod board that
 * called the same reply "confirmed".
 * `day` and `time` format an ISO day and a timestamp in the viewer's words.
 */
export function checkinNote(
  provenance: CheckinProvenance,
  input: {
    source: StatusSource | undefined;
    confirmedAt: string | null | undefined;
    developerConfirmed: boolean;
    summary?: string | null;
  },
  say: { day: (iso: string) => string; time: (iso: string) => string },
): string {
  if (provenance.kind === "none") return "no check-in on record";
  const when = input.confirmedAt ? ` · ${say.time(input.confirmedAt)}` : "";
  if (provenance.kind === "carried" && provenance.from) {
    return `${REPLY_WORDS.carried} from ${say.day(provenance.from)} · no reply today`;
  }
  if (repliedWithoutStatus(input.summary, input.source)) {
    return `${REPLY_WORDS.withoutStatus} · current status unknown`;
  }
  switch (input.source) {
    case "confirmed":
      return input.developerConfirmed
        ? `${REPLY_WORDS.confirmed} by you${when}`
        : `${REPLY_WORDS.replied} in chat, not confirmed${when}`;
    case "partial":
      return `${REPLY_WORDS.partly} in chat${when}`;
    case "inferred":
      return `no reply today · ${REPLY_WORDS.inferred} from delivery signals`;
    case "stale":
      return "no reply today · the last update is carried over";
    default:
      return "no reply today";
  }
}

/**
 * What confirming does, said under the buttons: for an earlier day's status
 * that it says the update still holds today; for a reply that stands today but
 * has not been confirmed, that the reply already counts, so a person who sees
 * "Confirm check-in" beside "replied" knows what it adds. Not on a past day:
 * nothing can be confirmed then, so telling the person what confirming would do
 * is an instruction they cannot follow.
 */
export function confirmCaption(
  provenance: CheckinProvenance,
  day: (iso: string) => string,
  pastDay = false,
  repliedNotConfirmed = false,
) {
  if (pastDay) return null;
  if (provenance.kind === "today" && repliedNotConfirmed && !provenance.confirmedToday) {
    return "Your reply counts as today's status. Confirm says what was recorded from it is right; correct it if not.";
  }
  if (provenance.kind !== "carried" || !provenance.from) return null;
  return `Confirming says the update from ${day(provenance.from)} still holds today, blockers included.`;
}

/**
 * How the person's pods read the check-in, or null when there is nothing to
 * flag: it is replied, still loading, there is no pod for it to reach, or a
 * past day is shown. "Until you confirm or correct it" is advice for today: on
 * a past day both buttons are off, and the line sat beside them anyway. The
 * word is the one the pod board uses for the same person (`boardWord`).
 */
export function checkinHint(input: {
  state: CheckinState;
  source: StatusSource | undefined;
  statusAsOf: string | null | undefined;
  summary: string | null | undefined;
  today: string;
  podNames: string[];
  pastDay?: boolean;
}): string | null {
  const { state, podNames } = input;
  if (input.pastDay || state === "confirmed" || podNames.length === 0) return null;
  const pods =
    podNames.length === 1
      ? podNames[0]
      : podNames.length === 2
        ? `${podNames[0]} and ${podNames[1]}`
        : `Your ${podNames.length} pods`;
  const shows = podNames.length === 1 ? "shows" : "show";
  const { word } = boardWord(
    {
      state,
      source: input.source ?? "unknown",
      status_as_of: input.statusAsOf ?? null,
      summary: input.summary ?? "",
    },
    input.today,
  );
  if (word === REPLY_WORDS.withoutStatus) {
    return `${pods} ${shows} that you replied without a status, so your status stays unknown until you confirm or correct it.`;
  }
  if (state === "missing") {
    return `${pods} ${shows} no reply from you. Silence is never read as green.`;
  }
  return `${pods} ${shows} your check-in as ${word} until you confirm or correct it.`;
}

/** What the card at the foot of Today says about why the check-in matters. */
export function whyCheckinMatters(pastDay: boolean): string {
  return pastDay
    ? "Your check-in fed every rollup above you: pod, project and program. Silence is never read as green: a status nobody confirmed stayed visible as carried forward, so leaders saw what was real."
    : "Your check-in feeds every rollup above you: pod, project and program. Silence is never read as green: a status you have not confirmed stays visible as carried forward until you confirm or correct it, so leaders see what is real.";
}

/**
 * What an empty "Your check-in" says, from the detail of the 404 that
 * `/me/status` answers with. The server answers 404 both for someone with no
 * member record and for a member with no status on record yet, and says which in
 * the detail ("status is not available: no member record for this person" or
 * "...: no status on record yet for this member"). Only the first is told to ask
 * an admin; it used to take a second request, the own check-in preference that
 * 404s only without a record, to tell them apart.
 *
 * A server that has the plain old detail ("status is not available") cannot be
 * told apart, so it gets words that are true of both.
 */
export function noStatusWords(detail: string | null | undefined): string {
  const text = (detail ?? "").toLowerCase();
  if (text.includes("no member record")) {
    return "You have no member record yet, and only members are asked to check in. An admin adds you under Admin → Directory.";
  }
  if (text.includes("no status on record yet")) {
    return "No check-in yet. Your first one comes in chat at your check-in time.";
  }
  return "No check-in to show. Only members are asked to check in, and a member's first one comes in chat at their check-in time. If you are no member yet, an admin adds you under Admin → Directory.";
}

/** Sources whose summary is the person's own words; the rest is wording the system wrote. */
export function isOwnWords(source: StatusSource | undefined): boolean {
  return source === "confirmed" || source === "partial";
}

/**
 * What makes two blocker texts the same blocker to the server (backend
 * `normalize_blocker_key`): whitespace collapsed, lowercased, trailing `.` and
 * `!` dropped.
 */
export function blockerKey(description: string): string {
  return description
    .trim()
    .split(/\s+/)
    .join(" ")
    .toLowerCase()
    .replace(/[.!]+$/, "");
}

/** A blocker as the correction form lists it: with its id when the server gave one. */
export type BlockerRow = {
  key: string;
  blocker_id: string | null;
  description: string;
  work_item_id: string | null;
  pod_id: string | null;
  age_days: number | null;
};

/**
 * The open blockers a correction must restate. A correction is a full
 * statement, so one left out is resolved: when the server gives no structured
 * details the flat list stands in (minus the no-reply placeholder), or those
 * blockers would be closed by saving something else.
 */
export function blockerRows(
  status: Pick<MyStatusResponse, "blockers" | "blocker_details">,
): BlockerRow[] {
  const details: BlockerDetailDto[] = status.blocker_details ?? [];
  if (details.length > 0) {
    return details.map((detail) => ({
      key: detail.blocker_id,
      blocker_id: detail.blocker_id,
      description: detail.description,
      work_item_id: detail.work_item_id,
      pod_id: detail.pod_id,
      age_days: detail.age_days,
    }));
  }
  return status.blockers
    .filter((description) => description !== NO_REPLY_BLOCKER)
    .map((description, index) => ({
      key: `flat-${index}`,
      blocker_id: null,
      description,
      work_item_id: null,
      pod_id: null,
      age_days: null,
    }));
}

/** Backend limits (BlockerCorrectionItemDto, StatusCorrectionRequest). */
export const MAX_BLOCKER_TEXT = 500;
export const MAX_BLOCKERS = 20;

export type CorrectionDraft = {
  summary: string;
  eta: string;
  /** Keys of the rows marked resolved. */
  resolved: string[];
  added: string;
};

/**
 * The request a correction sends, or what to tell the person is wrong first:
 * the same rules the server holds, said before the round trip.
 */
export function buildCorrection(
  rows: BlockerRow[],
  draft: CorrectionDraft,
): { ok: true; body: StatusCorrectionRequest } | { ok: false; message: string } {
  const summary = draft.summary.trim();
  if (!summary) return { ok: false, message: "Say what you did and what's next." };

  const etaText = draft.eta.trim();
  let eta: number | null = null;
  if (etaText !== "") {
    eta = Number(etaText);
    if (!Number.isInteger(eta)) {
      return { ok: false, message: "The ETA change is a whole number of days, like 2 or -1." };
    }
  }

  const added = draft.added.trim();
  if (added.length > MAX_BLOCKER_TEXT) {
    return { ok: false, message: `A blocker is at most ${MAX_BLOCKER_TEXT} characters.` };
  }
  // A listed blocker that carries no id (the flat list of an older status) is matched by
  // its wording alone, so a new one worded like it would be counted as it. One that
  // carries an id is matched by that id, and the new one stays a blocker of its own: it
  // is refused only when it would be a second copy of one kept open.
  const same = added ? rows.find((row) => blockerKey(row.description) === blockerKey(added)) : null;
  if (same) {
    const resolved = draft.resolved.includes(same.key);
    if (same.blocker_id === null) {
      return {
        ok: false,
        message: resolved
          ? "That is the blocker you marked resolved. Untick it to keep it open."
          : "That blocker is already on your list.",
      };
    }
    if (!resolved) return { ok: false, message: "That blocker is already on your list." };
  }
  if (rows.length + (added ? 1 : 0) > MAX_BLOCKERS) {
    return { ok: false, message: `At most ${MAX_BLOCKERS} blockers can be listed at once.` };
  }
  // Two open blockers worded alike can only be told apart by their ids. Without ids the
  // server reads one report per wording and closes an open blocker left out, so a
  // correction could only close both: say so before the round trip.
  const clash = duplicateKept(
    rows.filter((row) => row.blocker_id === null),
    draft.resolved,
  );
  if (clash) {
    return {
      ok: false,
      message: `Two of your blockers read the same ("${clash}"), so a correction can't keep one open and close the other. Mark both resolved, then add the one that still stands in your own words.`,
    };
  }

  return {
    ok: true,
    body: {
      summary,
      eta_change_days: eta,
      blocker_items: [
        // The wording, whether it is resolved, and the id the status named for it: the
        // server matches a restated blocker by that id first, so two blockers on one work
        // item, or worded alike, stay apart and a kept one keeps its age. Never the work
        // item or pod shown here: those are what the server inferred, and it keeps the
        // ones it stored.
        ...rows.map((row) => ({
          description: row.description,
          resolved: draft.resolved.includes(row.key),
          ...(row.blocker_id === null ? {} : { blocker_id: row.blocker_id }),
        })),
        ...(added ? [{ description: added, resolved: false }] : []),
      ],
    },
  };
}

/** The wording two rows share while at least one of them is kept open, or null. */
function duplicateKept(rows: BlockerRow[], resolved: string[]): string | null {
  const seen = new Map<string, BlockerRow[]>();
  for (const row of rows) {
    const key = blockerKey(row.description);
    seen.set(key, [...(seen.get(key) ?? []), row]);
  }
  for (const group of seen.values()) {
    if (group.length > 1 && group.some((row) => !resolved.includes(row.key))) {
      return group[0].description;
    }
  }
  return null;
}
