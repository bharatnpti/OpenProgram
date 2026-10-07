// Pure rules for a person's own check-in schedule, type imports and
// extensioned imports only so `node --test` can run them.
import type {
  CheckinPreferenceResponse,
  SelfCheckinPreferenceUpdateRequest,
} from "../../api/schema";
import { weekdaysLabel } from "../../lib/format.ts";
import { currentZoneName } from "../../lib/zones.ts";

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
  /** The days the bot asks, Monday 0: the team's while `teamDays`, else one's own. */
  weekdays: number[];
  /** One's own time zone, or null to follow the team's. */
  timezone: string | null;
}

export function draftFrom(preference: CheckinPreferenceResponse): ScheduleDraft {
  // `inherited` lists the fields not set for this person: their values are the team's.
  return {
    teamDays: preference.inherited.includes("weekdays"),
    weekdays: sortedDays(preference.weekdays),
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
 * What the dialog says about the time of the check-in. It is not the person's
 * to set: the bot asks everyone at one clock time, on each person's own clock,
 * so it is read out here rather than left out.
 */
export function askTimeWords(
  preference: Pick<CheckinPreferenceResponse, "local_time" | "timezone" | "defaults">,
): string {
  const zone = preference.timezone ?? preference.defaults.timezone;
  return `The bot asks you at ${clockTime(preference.local_time)} ${zone} time, the same time for your whole team, so the time isn't yours to set. You choose the days it asks you and your time zone.`;
}

/** "Mon–Fri", "Mon, Wed, Fri", "Every day". */
export function daysWords(weekdays: number[]): string {
  const words = weekdaysLabel(weekdays);
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** A zone as the dialog names it: the person's own, or the team's with its name. */
export function zoneWords(timezone: string | null, teamZone: string): string {
  return timezone ?? `your team's (${teamZone})`;
}

/** The menu line under "Check-in schedule": "Mon–Fri · Europe/Berlin". */
export function scheduleSummary(preference: CheckinPreferenceResponse): string {
  const draft = draftFrom(preference);
  const zone = draft.timezone ?? `team time zone (${preference.defaults.timezone})`;
  return `${daysWords(draft.weekdays)} · ${zone}`;
}

/** What a save would change, in words, one line per field. */
export function changeLines(
  initial: ScheduleDraft,
  draft: ScheduleDraft,
  teamZone: string,
): string[] {
  const changes = scheduleChanges(initial, draft);
  const days = (d: ScheduleDraft) =>
    d.teamDays ? `your team's (${daysWords(d.weekdays)})` : daysWords(d.weekdays);
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
