// Admin › Check-ins: the switch "Fill the day from Jira for people who were not
// asked", in words. Pure, type imports only, so `node --test` runs it directly.
// The rules are the backend's (core/application/fill_day.py): on by default, a
// filled day is inferred from what moved in Jira and Git, or stale or unknown,
// and never green.
import type { CheckinFillResponse } from "../../api/schema";

export const FILL_DAY_KEY = ["config", "checkin-fill"] as const;

export const FILL_DAY_LABEL = "Fill the day from Jira for people who were not asked";

/** What the switch does now, in one or two sentences. */
export function fillDayLine(fill: Pick<CheckinFillResponse, "enabled" | "deployment_enabled">) {
  if (!fill.deployment_enabled) {
    return "Off for this deployment, whatever is set here: people the check-in does not reach have no status for the day.";
  }
  if (!fill.enabled) {
    return "Off: people the check-in does not reach have no status for the day.";
  }
  return "On: when the check-in does not reach someone, or is not sent at all, their day is inferred from what moved in Jira and Git once their nudge would have gone out. It says so in its first words (not asked, not reached) and stays amber, never green.";
}

/** "On by default", or "Set by Asha Rao on Tue 6 Oct". */
export function fillDaySource(
  fill: Pick<CheckinFillResponse, "source" | "updated_at" | "updated_by">,
  say: { day: (iso: string) => string; name: (id: string) => string | null },
): string {
  if (fill.source === "default") return "On by default.";
  // An id no member's name resolves stays an id, never a guessed name.
  const who = fill.updated_by ? (say.name(fill.updated_by) ?? fill.updated_by) : "an admin";
  const when = fill.updated_at ? ` on ${say.day(fill.updated_at)}` : "";
  return `Set by ${who}${when}.`;
}

/** The toast after a save. */
export function fillDaySaved(enabled: boolean): string {
  return enabled
    ? "Saved. Days are filled from Jira for people the check-in does not reach."
    : "Saved. People the check-in does not reach keep no status for the day.";
}
