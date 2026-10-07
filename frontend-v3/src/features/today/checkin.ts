// Only type imports here: this module runs under `node --test` as written,
// which strips types but resolves no extensionless runtime import.
import type {
  BlockerDetailDto,
  MyStatusResponse,
  PodCheckinsResponse,
  StatusCorrectionRequest,
  StatusSource,
} from "../../api/schema";

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
 * The line in the card's corner: what the status is and when it is from.
 * `day` and `time` format an ISO day and a timestamp in the viewer's words.
 */
export function checkinNote(
  provenance: CheckinProvenance,
  input: {
    source: StatusSource | undefined;
    confirmedAt: string | null | undefined;
    developerConfirmed: boolean;
  },
  say: { day: (iso: string) => string; time: (iso: string) => string },
): string {
  if (provenance.kind === "none") return "no check-in on record";
  const when = input.confirmedAt ? ` · ${say.time(input.confirmedAt)}` : "";
  if (provenance.kind === "carried" && provenance.from) {
    return `carried forward from ${say.day(provenance.from)} · not confirmed today`;
  }
  switch (input.source) {
    case "confirmed":
      return input.developerConfirmed ? `confirmed by you${when}` : `answered in chat${when}`;
    case "partial":
      return `partly answered${when}`;
    case "inferred":
      return "inferred from delivery signals";
    case "stale":
      return "no reply today · the last update is carried over";
    default:
      return "no reply today";
  }
}

/**
 * What confirming does, said under the buttons when the status is an earlier
 * day's. Not on a past day: nothing can be confirmed then, so telling the
 * person what confirming would do is an instruction they cannot follow.
 */
export function confirmCaption(
  provenance: CheckinProvenance,
  day: (iso: string) => string,
  pastDay = false,
) {
  if (pastDay || provenance.kind !== "carried" || !provenance.from) return null;
  return `Confirming says the update from ${day(provenance.from)} still holds today, blockers included.`;
}

/**
 * How the person's pods read the check-in, or null when there is nothing to
 * flag: it is confirmed, still loading, there is no pod for it to reach, or a
 * past day is shown. "Until you confirm or correct it" is advice for today: on
 * a past day both buttons are off, and the line sat beside them anyway.
 */
export function checkinHint(
  state: CheckinState,
  podNames: string[],
  pastDay = false,
): string | null {
  if (pastDay || state === "confirmed" || podNames.length === 0) return null;
  const pods =
    podNames.length === 1
      ? podNames[0]
      : podNames.length === 2
        ? `${podNames[0]} and ${podNames[1]}`
        : `Your ${podNames.length} pods`;
  const shows = podNames.length === 1 ? "shows" : "show";
  return state === "missing"
    ? `${pods} ${shows} your check-in as missing. Silence is never read as green.`
    : `${pods} ${shows} your check-in as ${state} until you confirm or correct it.`;
}

/** What the card at the foot of Today says about why the check-in matters. */
export function whyCheckinMatters(pastDay: boolean): string {
  return pastDay
    ? "Your check-in fed every rollup above you: pod, project and program. Silence is never read as green: a status nobody confirmed stayed visible as stale, so leaders saw what was real."
    : "Your check-in feeds every rollup above you: pod, project and program. Silence is never read as green: an unconfirmed status stays visible as stale until you confirm or correct it, so leaders see what is real.";
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
  // The server treats a blocker worded the same as one already listed as that
  // blocker, so adding it while resolving it would close it, not keep it open.
  const same = added ? rows.find((row) => blockerKey(row.description) === blockerKey(added)) : null;
  if (same) {
    return {
      ok: false,
      message: draft.resolved.includes(same.key)
        ? "That is the blocker you marked resolved. Untick it to keep it open."
        : "That blocker is already on your list.",
    };
  }
  if (rows.length + (added ? 1 : 0) > MAX_BLOCKERS) {
    return { ok: false, message: `At most ${MAX_BLOCKERS} blockers can be listed at once.` };
  }
  // The server reads one report per wording and closes an open blocker left
  // out, so of two open blockers worded alike a correction can only close both.
  const clash = duplicateKept(rows, draft.resolved);
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
        // Only what the person says: the wording and whether it is resolved.
        // The server matches a restated blocker by its wording and keeps the
        // work item and pod it stored. The work item shown here is the one the
        // server inferred, and sending it back would match the report to
        // another blocker on the same work item.
        ...rows.map((row) => ({
          description: row.description,
          resolved: draft.resolved.includes(row.key),
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

/** The scrum master's line under a person on the pod board. */
export function podCheckinMeta(
  developer: Pick<
    PodCheckinsResponse["developers"][number],
    "state" | "source" | "status_as_of" | "summary"
  >,
  asOf: string,
  say: { day: (iso: string) => string },
): string {
  const { state, source, status_as_of: from, summary } = developer;
  if (state === "missing") return "no status yet";
  let lead: string;
  if (from && from !== asOf) {
    // The status is an earlier day's, carried forward: name the day, and what
    // it was then, so "confirmed" is never read as "answered today".
    const day = say.day(from);
    lead =
      source === "confirmed"
        ? `confirmed ${day}, nothing today`
        : source === "partial"
          ? `partly answered ${day}, nothing today`
          : source === "inferred"
            ? `inferred ${day}, nothing today`
            : `no reply since ${day}`;
  } else if (state === "confirmed") {
    lead = "confirmed today";
  } else if (state === "partial") {
    lead = "partly answered today";
  } else {
    lead = source === "inferred" ? "inferred from delivery signals" : "no reply today";
  }
  return summary ? `${lead} · ${summary}` : lead;
}
