// Only type imports here: node --test strips types but cannot resolve
// extensionless runtime imports.
import type {
  CheckInPreferenceField,
  CheckinPreferenceResponse,
  CheckinPreferenceUpdateRequest,
} from "../../api/schema";

/**
 * One member's check-in preference as the Change dialog edits it.
 *
 * A field either follows the team default (`inherited`) or is set for the
 * member. Saving sends only the fields someone touched: one put back to the
 * default goes as `null`, which the API reads as "follow the default again";
 * one left alone is not sent, so a save never rewrites a stored value it only
 * displayed and never copies a team default into the member's row.
 */

/** The fields an admin sets; the per-person time is unused (one tenant-wide send time). */
export type PrefField = Exclude<CheckInPreferenceField, "local_time">;

export const PREF_FIELDS: PrefField[] = [
  "weekdays",
  "timezone",
  "reply_wait_seconds",
  "final_reply_wait_seconds",
];

export const FIELD_NAMES: Record<PrefField, string> = {
  weekdays: "days",
  timezone: "time zone",
  reply_wait_seconds: "nudge wait",
  final_reply_wait_seconds: "give-up wait",
};

/** The largest wait the API stores, in seconds. */
const MAX_WAIT_SECONDS = 2_147_483_647;

export type PrefDraft = {
  weekdays: number[];
  timezone: string;
  /** Minutes as typed. */
  nudgeMinutes: string;
  /** Minutes as typed. */
  giveUpMinutes: string;
  inherited: PrefField[];
  /** Fields edited or put back to the default in this dialog; only these are sent. */
  touched: PrefField[];
};

const isPrefField = (field: CheckInPreferenceField): field is PrefField =>
  (PREF_FIELDS as CheckInPreferenceField[]).includes(field);

/** Seconds as minutes for the input, without inventing a rounding: 90 s is "1.5". */
export function minutesText(seconds: number): string {
  return String(Math.round((seconds / 60) * 100) / 100);
}

/** Typed minutes as whole seconds, or null when it is not a wait the API takes. */
export function secondsFromMinutes(text: string): number | null {
  const clean = text.trim();
  if (!/^\d+(\.\d+)?$/.test(clean)) return null;
  const seconds = Math.round(Number(clean) * 60);
  return seconds <= MAX_WAIT_SECONDS ? seconds : null;
}

/**
 * The days offered as a member's days, Monday 0 (`send`, the tenant's one
 * schedule). On a weekly schedule only the days the bot sends on, since a day
 * it never sends on asks nobody; on any other (dates such as 1 Jan, or a cron
 * that is neither) every day, since a member's day only skips a send that
 * falls on it.
 */
export function sendDays(pref: Pick<CheckinPreferenceResponse, "send">): number[] {
  const { kind, weekdays } = pref.send;
  const days = kind === "weekly" && weekdays ? weekdays : [0, 1, 2, 3, 4, 5, 6];
  return [...new Set(days)].sort((a, b) => a - b);
}

/** Of `weekdays`, the days offered: on a weekly schedule, the days they are really asked. */
export function askedDays(weekdays: number[], pref: Pick<CheckinPreferenceResponse, "send">) {
  const sent = sendDays(pref);
  return [...new Set(weekdays)].filter((day) => sent.includes(day)).sort((a, b) => a - b);
}

/**
 * Under the Change dialog's day chips on a schedule that isn't weekly: what
 * the days do there. Null on a weekly one, whose chips are its send days.
 * While the scheduled send is off (`kind` off) they do nothing until it is on.
 */
export function daysNote(pref: Pick<CheckinPreferenceResponse, "send">): string | null {
  if (pref.send.kind === "weekly") return null;
  if (pref.send.kind === "off") {
    return "Check-ins aren't sent on a schedule right now, so these days only matter once the scheduled send is on again.";
  }
  return "Check-ins aren't on a weekly schedule, so these days only skip a send that falls on a day left off.";
}

export function draftFrom(pref: CheckinPreferenceResponse): PrefDraft {
  return {
    // A stored day the bot never sends on is not offered; a save that changes
    // the days sends only days it sends on.
    weekdays: askedDays(pref.weekdays, pref),
    timezone: pref.timezone ?? pref.defaults.timezone,
    nudgeMinutes: minutesText(pref.reply_wait_seconds),
    giveUpMinutes: minutesText(pref.final_reply_wait_seconds),
    inherited: pref.inherited.filter(isPrefField),
    touched: [],
  };
}

/** The value the draft holds for a field, as the API counts it; null while it can't be saved. */
export function draftValue(draft: PrefDraft, field: PrefField): number[] | string | number | null {
  if (field === "weekdays") return draft.weekdays;
  if (field === "timezone") return draft.timezone.trim() || null;
  if (field === "reply_wait_seconds") return secondsFromMinutes(draft.nudgeMinutes);
  return secondsFromMinutes(draft.giveUpMinutes);
}

/** The team default for a field. */
export function defaultValue(pref: CheckinPreferenceResponse, field: PrefField) {
  return pref.defaults[field];
}

/** The value stored for the member, as the API reports it (the default when inherited). */
function storedValue(pref: CheckinPreferenceResponse, field: PrefField) {
  if (field === "timezone") return pref.timezone ?? pref.defaults.timezone;
  return pref[field];
}

export function sameValue(field: PrefField, left: unknown, right: unknown): boolean {
  if (field === "weekdays") {
    const sorted = (value: unknown) => [...((value as number[]) ?? [])].sort().join();
    return sorted(left) === sorted(right);
  }
  return left === right;
}

const withField = (fields: PrefField[], field: PrefField, on: boolean) => {
  const others = fields.filter((item) => item !== field);
  return on ? PREF_FIELDS.filter((item) => item === field || others.includes(item)) : others;
};

function setRaw(draft: PrefDraft, field: PrefField, value: number[] | string): PrefDraft {
  if (field === "weekdays") return { ...draft, weekdays: [...(value as number[])].sort() };
  if (field === "timezone") return { ...draft, timezone: value as string };
  if (field === "reply_wait_seconds") return { ...draft, nudgeMinutes: value as string };
  return { ...draft, giveUpMinutes: value as string };
}

/**
 * An edit sets the field for the member, unless it puts back the team default
 * on a field that was following it: that one keeps following, so a change and
 * an undo don't pin today's default to the member.
 */
export function editField(
  draft: PrefDraft,
  pref: CheckinPreferenceResponse,
  field: PrefField,
  value: number[] | string,
): PrefDraft {
  const next = setRaw(draft, field, value);
  const follows =
    pref.inherited.includes(field) &&
    sameValue(field, draftValue(next, field), defaultValue(pref, field));
  return {
    ...next,
    inherited: withField(next.inherited, field, follows),
    touched: withField(next.touched, field, true),
  };
}

/** Back to the team default: the field shows the default and follows it from the save on. */
export function toDefault(
  draft: PrefDraft,
  pref: CheckinPreferenceResponse,
  field: PrefField,
): PrefDraft {
  const value = defaultValue(pref, field);
  const raw =
    field === "weekdays"
      ? (value as number[])
      : field === "timezone"
        ? (value as string)
        : minutesText(value as number);
  return {
    ...setRaw(draft, field, raw),
    inherited: withField(draft.inherited, field, true),
    touched: withField(draft.touched, field, true),
  };
}

export function toEveryDefault(draft: PrefDraft, pref: CheckinPreferenceResponse): PrefDraft {
  return PREF_FIELDS.reduce((current, field) => toDefault(current, pref, field), draft);
}

/**
 * The PUT body. A touched field now following the default is sent as `null`
 * when it was set before; a touched field set for the member is sent when it
 * was following the default before or its value changed. Nothing else is sent.
 */
export function changesFrom(
  pref: CheckinPreferenceResponse,
  draft: PrefDraft,
): CheckinPreferenceUpdateRequest {
  const changes: CheckinPreferenceUpdateRequest = {};
  for (const field of draft.touched) {
    const followsNow = draft.inherited.includes(field);
    const followedBefore = pref.inherited.includes(field);
    if (followsNow) {
      if (!followedBefore) Object.assign(changes, { [field]: null });
      continue;
    }
    const value = draftValue(draft, field);
    if (value === null) continue;
    if (followedBefore || !sameValue(field, value, storedValue(pref, field))) {
      Object.assign(changes, { [field]: value });
    }
  }
  return changes;
}

/** Whether a name is a time zone this browser knows; the server checks it again. */
export function isTimeZone(name: string): boolean {
  try {
    new Intl.DateTimeFormat("en-GB", { timeZone: name.trim() });
    return name.trim().length > 0;
  } catch {
    return false;
  }
}

/** What would stop the save, in the words the form shows. */
export function draftProblems(draft: PrefDraft): string[] {
  const problems: string[] = [];
  if (draft.weekdays.length === 0) {
    problems.push("Pick at least one day: with none, their check-ins would stop without a word.");
  }
  if (!draft.inherited.includes("timezone")) {
    if (!draft.timezone.trim()) problems.push("Enter a time zone, or use the team default.");
    else if (!isTimeZone(draft.timezone)) {
      problems.push(`“${draft.timezone.trim()}” is not a time zone. Pick one from the list.`);
    }
  }
  if (secondsFromMinutes(draft.nudgeMinutes) === null) {
    problems.push("Nudge after is a number of minutes, such as 240.");
  }
  if (secondsFromMinutes(draft.giveUpMinutes) === null) {
    problems.push("Give up after is a number of minutes, such as 480.");
  }
  return problems;
}

/** "Follows the team default for days and time zone", or that every field is set. */
export function inheritedSummary(inherited: CheckInPreferenceField[]): string {
  const fields = PREF_FIELDS.filter((field) => inherited.includes(field));
  if (fields.length === PREF_FIELDS.length) return "Follows every team default";
  if (fields.length === 0) return "Every value set for this member";
  const names = fields.map((field) => FIELD_NAMES[field]);
  const list =
    names.length === 1
      ? names[0]
      : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  return `Follows the team default for ${list}`;
}
