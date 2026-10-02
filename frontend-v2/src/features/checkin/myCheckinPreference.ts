import { useQuery } from "@tanstack/react-query";

import { ApiError, apiClient } from "../../api/client";
import type {
  CheckinPreferenceResponse,
  SelfCheckinPreferenceUpdateRequest,
} from "../../api/schema";
import { weekdayOptions } from "../admin/adminTypes";

export const myCheckinPreferenceKey = ["me", "checkin-preference"] as const;

/**
 * The signed-in person's own check-in preference.
 *
 * The backend answers 404 when the person has no member record. Check-ins go
 * only to members, so that person has nothing to set, and `noCheckin` says to
 * hide the control rather than show an error.
 */
export function useMyCheckinPreference() {
  return useQuery({
    queryKey: myCheckinPreferenceKey,
    queryFn: () => apiClient.checkinPreference(),
    retry: (failureCount, error) => !isNoCheckin(error) && failureCount < 1,
    staleTime: 60_000,
  });
}

/** No member record (404) or no own-work scope (403): nobody asks this person. */
export function isNoCheckin(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.status === 403);
}

/**
 * The parts of a preference a person may change for themselves. A `null`
 * time zone follows the team's, and changes when the team's does.
 */
export interface ScheduleDraft {
  weekdays: number[];
  timezone: string | null;
}

export function draftFrom(preference: CheckinPreferenceResponse): ScheduleDraft {
  // The response gives the zone that applies; `inherited` says it is the team's.
  const followsTeam = preference.inherited.includes("timezone");
  return {
    weekdays: sortedDays(preference.weekdays),
    timezone: followsTeam ? null : preference.timezone,
  };
}

/**
 * Only the fields that changed, so a save never rewrites anything else.
 *
 * The backend keeps every field a request leaves out. Sending back values that
 * were only displayed is how the admin sliders cut stored 4 h / 8 h reply
 * windows to their 1 h / 2 h maximum. The self endpoint refuses the check-in
 * time and reply windows outright, so its request type has neither.
 */
export function scheduleChanges(
  initial: ScheduleDraft,
  draft: ScheduleDraft,
): SelfCheckinPreferenceUpdateRequest {
  const changes: SelfCheckinPreferenceUpdateRequest = {};
  if (!sameDays(initial.weekdays, draft.weekdays)) {
    changes.weekdays = sortedDays(draft.weekdays);
  }
  if (initial.timezone !== draft.timezone) {
    changes.timezone = draft.timezone;
  }
  return changes;
}

export function sortedDays(weekdays: number[]): number[] {
  return Array.from(new Set(weekdays)).sort((a, b) => a - b);
}

function sameDays(left: number[], right: number[]): boolean {
  const a = sortedDays(left);
  const b = sortedDays(right);
  return a.length === b.length && a.every((day, index) => day === b[index]);
}

/** Days in plain words: "Mon–Fri", "Mon, Wed, Fri", "every day". */
export function daysLabel(weekdays: number[]): string {
  const days = sortedDays(weekdays).filter((day) =>
    weekdayOptions.some((option) => option.value === day),
  );
  if (days.length === 0) return "no days";
  if (days.length === weekdayOptions.length) return "every day";
  const consecutive = days.every((day, index) => index === 0 || day === days[index - 1] + 1);
  if (consecutive && days.length >= 3) {
    return `${dayName(days[0])}–${dayName(days[days.length - 1])}`;
  }
  return days.map(dayName).join(", ");
}

function dayName(day: number): string {
  return weekdayOptions.find((option) => option.value === day)?.label ?? String(day);
}

/** A stored null means the team's default zone applies. */
export function timezoneLabel(timezone: string | null): string {
  return timezone ?? "team time zone";
}

/** The zone this browser runs in, when it says. */
export function deviceTimezone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null;
  }
}

/**
 * IANA zones to pick from, always including the stored one.
 *
 * The list comes from the browser. A stored zone the browser doesn't list (an
 * older alias, say) is kept as an option, so opening the dialog never swaps it
 * for another.
 */
export function timezoneOptions(...keep: (string | null)[]): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (key: "timeZone") => string[] };
  const zones = new Set<string>(["UTC"]);
  try {
    for (const zone of intl.supportedValuesOf?.("timeZone") ?? []) zones.add(zone);
  } catch {
    // An older browser: the stored and device zones below still make a list.
  }
  for (const zone of keep) {
    if (zone) zones.add(zone);
  }
  return Array.from(zones).sort();
}
