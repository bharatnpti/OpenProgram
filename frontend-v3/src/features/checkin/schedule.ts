// Pure rules for a person's own check-in schedule, type imports and
// extensioned imports only so `node --test` can run them.
import type {
  CheckinPreferenceResponse,
  CheckinSendResponse,
  SelfCheckinPreferenceUpdateRequest,
} from "../../api/schema";
import { weekdaysLabel } from "../../lib/format.ts";
import { clockIn, currentZoneName } from "../../lib/zones.ts";

/*
 * When the bot asks, as the backend does it (infra/workflows/schedule.py,
 * checkin_fanout.py, daily_checkin.py): one send for the whole tenant, on the
 * fan-out cron read in UTC (`send`, from OPENPROGRAM_CHECKIN_FANOUT_CRON). On a
 * send day everyone is asked at once, and someone whose own days leave out
 * that day (by the send's UTC date) is skipped. A member's stored `local_time`
 * is never used to send, so it is never shown as when they are asked; their
 * time zone decides which day a reply counts for (replies are matched to a
 * check-in by the member's own calendar day), not when they are asked.
 *
 * `send.kind` says what the schedule is, and the words say only that:
 * - weekly: one time on days of the week. Only those days are offered as a
 *   member's days, and all seven is "every day".
 * - dates: one time on listed dates ("1 Jan" when qa2 pauses its sends). It is
 *   no daily or weekly ask, so it says so; every day of the week is offered,
 *   since a member's days only skip a send that falls on one they leave off.
 * - other: steps, ranges and the like. A member reads "a schedule your admin
 *   set"; an admin reads the cron. Every day of the week is offered.
 * - off: the scheduled send is switched off (OPENPROGRAM_CHECKIN_FANOUT_ENABLED),
 *   so nobody is asked on a schedule. Everyone reads "Check-ins aren't sent on a
 *   schedule right now."; no time, cron or day is said as when the bot asks.
 *   Every day of the week is offered, and a member's days only matter once it
 *   is on again.
 * "Every day" is said only of a weekly schedule that sends on all seven days.
 */

type Send = CheckinSendResponse;

const EVERY_DAY = [0, 1, 2, 3, 4, 5, 6];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// More dates than this are said as days of months: "the 1st and 15th of Jan, Apr, Jul and Oct".
const DATES_LISTED = 6;
// Long enough for any date to come round, 29 February included.
const DAYS_AHEAD_FOR_DATES = 4 * 366;

/** Whether the bot asks on one time on days of the week, as the backend's default does. */
export function isWeekly(send: Pick<Send, "kind">): boolean {
  return send.kind === "weekly";
}

/** Whether the scheduled send is switched off, so the bot asks nobody on a schedule. */
export function isOff(send: Pick<Send, "kind">): boolean {
  return send.kind === "off";
}

/** Said first wherever the send is off, in the member's dialog and in Admin › Check-ins. */
export const SEND_OFF = "Check-ins aren't sent on a schedule right now.";

/**
 * The days offered as someone's days, Monday 0. On a weekly schedule only the
 * days it sends on, since a day it never sends on asks nobody; on any other,
 * every day, since a member's day only skips a send that falls on it.
 */
export function sendDays(send: Pick<Send, "kind" | "weekdays">): number[] {
  return isWeekly(send) && send.weekdays ? sortedDays(send.weekdays) : EVERY_DAY;
}

/**
 * The days that count for someone: their own (or the team's) that are
 * offered (`sendDays`). On a weekly schedule these are the days they are asked.
 */
export function askedDays(weekdays: number[], send: Pick<Send, "kind" | "weekdays">) {
  const offered = sendDays(send);
  return sortedDays(weekdays).filter((day) => offered.includes(day));
}

/**
 * "Mon–Fri", "Mon, Wed, Fri". All seven days are "every day" only when the bot
 * sends every day; on a schedule that isn't weekly they are "Mon–Sun", since
 * the bot doesn't ask on each of them.
 */
export function daysLabel(weekdays: number[], send: Pick<Send, "kind" | "weekdays">): string {
  const days = sortedDays(weekdays);
  const daily = isWeekly(send) && sendDays(send).length === EVERY_DAY.length;
  return days.length === EVERY_DAY.length && !daily ? "Mon–Sun" : weekdaysLabel(days);
}

/** "Mon–Fri", "Mon, Wed, Fri", "Every day" (daily sends only), "Mon–Sun". */
export function daysWords(weekdays: number[], send: Pick<Send, "kind" | "weekdays">): string {
  const words = daysLabel(weekdays, send);
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** "1st", "2nd", "3rd", "11th", "22nd". */
function ordinal(day: number): string {
  const tens = day % 100;
  if (tens >= 11 && tens <= 13) return `${day}th`;
  return `${day}${{ 1: "st", 2: "nd", 3: "rd" }[day % 10] ?? "th"}`;
}

/** "a", "a and b", "a, b and c". */
function listWords(items: string[]): string {
  return items.length <= 1
    ? (items[0] ?? "")
    : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

/**
 * The dates of a `dates` schedule, after "on": "1 Jan", "1 Jan and 1 Jul",
 * "the 1st and 15th of each month", "each day of Jan".
 */
export function datesWords(send: Pick<Send, "month_days" | "months">): string {
  const days = send.month_days ? sortedDays(send.month_days) : null;
  const months = send.months ? sortedDays(send.months).map((month) => MONTHS[month - 1]) : null;
  if (days && months) {
    if (days.length * months.length <= DATES_LISTED) {
      return listWords(months.flatMap((month) => days.map((day) => `${day} ${month}`)));
    }
    return `the ${listWords(days.map(ordinal))} of ${listWords(months)}`;
  }
  if (days) return `the ${listWords(days.map(ordinal))} of each month`;
  return months ? `each day of ${listWords(months)}` : "";
}

/** The next send after `now`; null for a schedule that isn't weekly or on dates, or is off. */
export function nextSendAt(
  send: Pick<Send, "kind" | "local_time" | "weekdays" | "month_days" | "months">,
  now: Date,
): Date | null {
  if (!send.local_time || (send.kind !== "weekly" && send.kind !== "dates")) return null;
  const [hour, minute, second] = send.local_time.split(":").map(Number);
  const matches = (at: Date) =>
    isWeekly(send)
      ? // getUTCDay counts from Sunday; these days count from Monday.
        sendDays(send).includes((at.getUTCDay() + 6) % 7)
      : (!send.month_days || send.month_days.includes(at.getUTCDate())) &&
        (!send.months || send.months.includes(at.getUTCMonth() + 1));
  const reach = isWeekly(send) ? 7 : DAYS_AHEAD_FOR_DATES;
  for (let ahead = 0; ahead <= reach; ahead += 1) {
    const at = new Date(
      Date.UTC(
        now.getUTCFullYear(),
        now.getUTCMonth(),
        now.getUTCDate() + ahead,
        hour,
        minute,
        second || 0,
      ),
    );
    if (matches(at) && at.getTime() > now.getTime()) return at;
  }
  return null;
}

/**
 * The send time, in UTC and, when it reads differently there, in `zone`:
 * "09:30 UTC (11:30 in Europe/Berlin)", "09:30 UTC (23:30 the day before in
 * Pacific/Honolulu)", "09:30 UTC". The zone's clock is the next send's, so a
 * change of summer time shows from the day it applies. Only for a schedule
 * with one time (weekly or dates); any other is said as its cron. Never
 * called for a send that is off, which has no time.
 */
export function sendTimeWords(
  send: Pick<
    Send,
    "kind" | "cron" | "timezone" | "local_time" | "weekdays" | "month_days" | "months"
  >,
  zone: string | null,
  now: Date,
): string {
  if (!send.local_time || (send.kind !== "weekly" && send.kind !== "dates")) {
    return `${send.cron} (${send.timezone})`;
  }
  const utc = `${clockTime(send.local_time)} ${send.timezone}`;
  const at = zone && zone !== send.timezone ? nextSendAt(send, now) : null;
  const local = at && zone ? clockIn(at, zone) : null;
  if (!local || (local.clock === clockTime(send.local_time) && local.dayShift === 0)) return utc;
  const day = local.dayShift < 0 ? " the day before" : local.dayShift > 0 ? " the next day" : "";
  return `${utc} (${local.clock}${day} in ${zone})`;
}

/**
 * When the bot asks everyone, after "asks everyone": "at 09:30 UTC (11:30 in
 * Europe/Berlin), Mon–Fri", "at 09:30 UTC, every day", "only on 1 Jan, at
 * 00:00 UTC (01:00 in Europe/Berlin)", or, for any other schedule, "on the
 * schedule 0 9-17 * * 1-5 (UTC)" (an admin's words; a member reads `askTimeWords`).
 */
export function sendWords(send: Send, zone: string | null, now: Date): string {
  const time = sendTimeWords(send, zone, now);
  if (send.kind === "weekly") return `at ${time}, ${daysLabel(sendDays(send), send)}`;
  if (send.kind === "dates") return `only on ${datesWords(send)}, at ${time}`;
  return `on the schedule ${time}`;
}

/** Said before a schedule that isn't weekly, so nobody reads a time as a daily ask. */
const NOT_WEEKLY = "Check-ins aren't on a weekly schedule right now.";

/**
 * Admin › Check-ins, after the team default: when the bot asks everyone, on the
 * admin's own clock (`zone`), and what a member's days and zone change.
 */
export function adminSendWords(send: Send, zone: string | null, now: Date = new Date()): string {
  if (isOff(send)) {
    // The cron is what applies once it is on again, so it isn't said as when the bot asks.
    return `${SEND_OFF} The scheduled send is switched off (OPENPROGRAM_CHECKIN_FANOUT_ENABLED), so the bot asks nobody until it is on again. A member's days only matter then; their time zone decides which day a reply counts for.`;
  }
  const effect = isWeekly(send)
    ? "A member's days decide whether they are asked that day"
    : "A member's days only skip them when a send falls on a day they leave off";
  const after = `${effect}; their time zone decides which day a reply counts for, not when they are asked.`;
  if (send.kind === "weekly") {
    return `The bot asks everyone ${sendWords(send, zone, now)}: one send for the whole tenant. ${after}`;
  }
  if (send.kind === "dates") {
    return `${NOT_WEEKLY} The bot asks everyone ${sendWords(send, zone, now)}: one send for the whole tenant. ${after}`;
  }
  return `The bot asks everyone on the cron schedule ${send.cron}, read in ${send.timezone}: one schedule for the whole tenant. It isn't a simple weekly time, so the cron is the only account of when it asks. ${after}`;
}

/** The person's own preference; one cache entry whatever day the console views. */
export const MY_CHECKIN_PREFERENCE_KEY = ["checkin-preference", "me"] as const;

/**
 * The two things a person may set for themselves (PUT /me/checkin-preference).
 * Either can follow the team: a team value changes when the team's does, while
 * a value of one's own stays put.
 */
export interface ScheduleDraft {
  /** Follow the team's days. */
  teamDays: boolean;
  /**
   * The days the bot asks, Monday 0: the team's while `teamDays`, else one's
   * own; only the days offered (`askedDays`).
   */
  weekdays: number[];
  /** One's own time zone, or null to follow the team's. */
  timezone: string | null;
}

export function draftFrom(preference: CheckinPreferenceResponse): ScheduleDraft {
  // `inherited` lists the fields not set for this person: their values are the team's.
  return {
    teamDays: preference.inherited.includes("weekdays"),
    // On a weekly schedule a stored day the bot never sends on asks nothing, so
    // it isn't offered; a save that changes the days sends only offered days.
    weekdays: askedDays(preference.weekdays, preference.send),
    timezone: preference.inherited.includes("timezone") ? null : preference.timezone,
  };
}

/**
 * Only what changed, so a save never rewrites anything else. A field sent as
 * null goes back to the team default; the backend keeps every field left out,
 * and refuses the time and reply windows outright (an admin sets those).
 */
export function scheduleChanges(
  initial: ScheduleDraft,
  draft: ScheduleDraft,
): SelfCheckinPreferenceUpdateRequest {
  const changes: SelfCheckinPreferenceUpdateRequest = {};
  const daysChanged =
    initial.teamDays !== draft.teamDays ||
    (!draft.teamDays && !sameDays(initial.weekdays, draft.weekdays));
  if (daysChanged) {
    changes.weekdays = draft.teamDays ? null : sortedDays(draft.weekdays);
  }
  if (initial.timezone !== draft.timezone) {
    changes.timezone = draft.timezone;
  }
  return changes;
}

/** Why the draft can't be saved, or null. The backend refuses no days too. */
export function scheduleProblem(draft: ScheduleDraft): string | null {
  return !draft.teamDays && draft.weekdays.length === 0
    ? "Pick at least one day, or follow your team's days."
    : null;
}

/** The hour and minute of a clock time the API sends ("09:30:00"): "09:30". */
export function clockTime(time: string): string {
  return time.slice(0, 5);
}

/**
 * What the dialog says about when the bot asks: the one send for everyone, in
 * UTC and on the clock of the person's time zone (`zone`, the one the dialog
 * shows), so the time is read out rather than left out, though it is not the
 * person's to set. A schedule that isn't weekly says so first; one that has no
 * one time is "a schedule your admin set" (an admin reads its cron).
 */
export function askTimeWords(send: Send, zone: string | null, now: Date = new Date()): string {
  if (isOff(send)) {
    return `${SEND_OFF} The bot won't ask you until your admin turns the scheduled send back on. You can still choose your days and your time zone: they apply from then.`;
  }
  const notYours = "so it isn't yours to set";
  if (send.kind === "weekly") {
    return `The bot asks everyone ${sendWords(send, zone, now)}: one time for the whole team, ${notYours}. You choose which of those days it asks you, and your time zone.`;
  }
  if (send.kind === "dates") {
    return `${NOT_WEEKLY} The bot asks everyone ${sendWords(send, zone, now)}: one time for the whole team, ${notYours}. You choose your days and your time zone.`;
  }
  return `The bot asks everyone on a schedule your admin set, the same for the whole team, ${notYours}. You choose your days and your time zone.`;
}

/** Under the time zone: what it changes, and that the send time isn't one of those things. */
export function zoneEffectWords(send: Send): string {
  const effect = "Decides which day your reply counts for. It doesn't change when the bot asks you";
  if (!send.local_time || (send.kind !== "weekly" && send.kind !== "dates")) return `${effect}.`;
  const time = `${clockTime(send.local_time)} ${send.timezone}`;
  const when = send.kind === "dates" ? `${time} on ${datesWords(send)}` : time;
  return `${effect}: that is ${when} for everyone.`;
}

/** The legend over the day chips: on a weekly schedule they are the days it asks you. */
export function daysLegendWords(send: Pick<Send, "kind">): string {
  return isWeekly(send) ? "Days the bot asks you" : "Your days";
}

/**
 * The line under the day chips. On a schedule that isn't weekly, every day is
 * offered, so it says that a day only skips a send that falls on it; while the
 * send is off, that the days only matter once it is on again.
 */
export function daysHintWords(send: Pick<Send, "kind">, teamDays: boolean): string {
  const following = "Following your team: when its days change, yours do too.";
  if (isOff(send)) {
    const later = "Your days only matter once check-ins are sent on a schedule again.";
    return `${later} ${teamDays ? following : "These stay yours if the team's change."}`;
  }
  if (isWeekly(send)) {
    return teamDays
      ? following
      : "On days you leave off, the bot doesn't ask you. These stay yours if the team's change.";
  }
  const skip =
    "Your days don't add asks: they only skip you when a send falls on a day you leave off.";
  return `${skip} ${teamDays ? following : "These stay yours if the team's change."}`;
}

/** A zone as the dialog names it: the person's own, or the team's with its name. */
export function zoneWords(timezone: string | null, teamZone: string): string {
  return timezone ?? `your team's (${teamZone})`;
}

/**
 * The menu line under "Check-in schedule". Weekly: the days they are asked,
 * "Mon–Fri · Europe/Berlin". Otherwise when the bot asks, and the days that
 * skip them: "Only on 1 Jan · skips Sat, Sun · Europe/Berlin", "On your admin's
 * schedule · Europe/Berlin". Off: "Not sent on a schedule now · Europe/Berlin",
 * with no days, since none of them is asked.
 */
export function scheduleSummary(preference: CheckinPreferenceResponse): string {
  const send = preference.send;
  const draft = draftFrom(preference);
  const zone = draft.timezone ?? `team time zone (${preference.defaults.timezone})`;
  if (isOff(send)) return `Not sent on a schedule now · ${zone}`;
  if (isWeekly(send)) return `${daysWords(draft.weekdays, send)} · ${zone}`;
  const when = send.kind === "dates" ? `Only on ${datesWords(send)}` : "On your admin's schedule";
  const skipped = EVERY_DAY.filter((day) => !draft.weekdays.includes(day));
  // No days at all can't be saved; say nothing rather than "skips every day".
  const skips =
    skipped.length > 0 && skipped.length < EVERY_DAY.length
      ? ` · skips ${weekdaysLabel(skipped)}`
      : "";
  return `${when}${skips} · ${zone}`;
}

/** What a save would change, in words, one line per field. */
export function changeLines(
  initial: ScheduleDraft,
  draft: ScheduleDraft,
  teamZone: string,
  send: Pick<Send, "kind" | "weekdays">,
): string[] {
  const changes = scheduleChanges(initial, draft);
  const days = (d: ScheduleDraft) =>
    d.teamDays ? `your team's (${daysWords(d.weekdays, send)})` : daysWords(d.weekdays, send);
  const lines: string[] = [];
  if ("weekdays" in changes) lines.push(`Days: ${days(initial)} → ${days(draft)}`);
  if ("timezone" in changes) {
    lines.push(
      `Time zone: ${zoneWords(initial.timezone, teamZone)} → ${zoneWords(draft.timezone, teamZone)}`,
    );
  }
  return lines;
}

export function sortedDays(weekdays: number[]): number[] {
  return Array.from(new Set(weekdays)).sort((a, b) => a - b);
}

export function toggleDay(weekdays: number[], day: number): number[] {
  return weekdays.includes(day)
    ? weekdays.filter((item) => item !== day)
    : sortedDays([...weekdays, day]);
}

function sameDays(left: number[], right: number[]): boolean {
  const a = sortedDays(left);
  const b = sortedDays(right);
  return a.length === b.length && a.every((day, index) => day === b[index]);
}

/**
 * Why nobody asks this person to check in. Check-ins go only to members (404
 * without a member record); every role may read its own work, so a 403 means
 * a sign-in with no role the console knows, and the server's reason is kept.
 */
export function noCheckinWords(status: number, message: string): string {
  if (status === 404) return "The bot doesn't ask you: you have no member record.";
  return `The bot doesn't ask you: your sign-in has no role that checks in. The server said: ${message}`;
}

/** "4 hours", "1 hour 30 minutes", "45 minutes": how long the bot waits for a reply. */
export function waitWords(seconds: number): string {
  const minutes = Math.round(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  const part = (n: number, unit: string) => `${n} ${unit}${n === 1 ? "" : "s"}`;
  if (hours === 0) return part(rest, "minute");
  return rest === 0 ? part(hours, "hour") : `${part(hours, "hour")} ${part(rest, "minute")}`;
}

/**
 * The backend's reasons for refusing a save (FastAPI's 422 detail), in its
 * own words without pydantic's "Value error, " prefix; null when it gave none.
 */
export function refusalWords(detail: unknown): string | null {
  const items = (detail as { detail?: unknown } | null)?.detail;
  if (typeof items === "string") return items;
  if (!Array.isArray(items)) return null;
  const messages = items
    .map((item) => (item as { msg?: unknown })?.msg)
    .filter((msg): msg is string => typeof msg === "string" && msg.length > 0)
    .map((msg) => msg.replace(/^Value error, /, ""));
  return messages.length > 0 ? messages.join("; ") : null;
}

/** The zone this browser runs in, under the name a current server knows; null when it doesn't say. */
export { deviceTimezone } from "../../lib/zones.ts";

/**
 * IANA zones to pick from, always including the ones given (the stored zone,
 * the device's), so opening the dialog never swaps a stored zone the browser
 * doesn't list for another.
 */
export function timezoneOptions(...keep: (string | null)[]): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (key: "timeZone") => string[] };
  const zones = new Set<string>(["UTC"]);
  try {
    // Under their current names: the browser lists a few by an old one the server rejects.
    for (const zone of intl.supportedValuesOf?.("timeZone") ?? []) zones.add(currentZoneName(zone));
  } catch {
    // An older browser: the zones below still make a list.
  }
  for (const zone of keep) {
    if (zone) zones.add(zone);
  }
  return Array.from(zones).sort();
}
