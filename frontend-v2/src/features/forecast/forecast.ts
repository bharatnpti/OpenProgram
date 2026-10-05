// Only type imports here: node --test strips types but cannot resolve
// extensionless runtime imports.
import type { BadgeTone } from "../../lib/status";
import type { CommitmentResponse, ScopeDeliveryResponse, Verdict } from "../../api/schema";

export const VERDICT_LABELS: Record<Verdict, string> = {
  on_track: "On track",
  at_risk: "At risk",
  off_track: "Off track",
  done: "Done",
  no_date: "No date set",
  not_enough_data: "Not enough data",
};

export const VERDICT_TONES: Record<Verdict, BadgeTone> = {
  on_track: "success",
  at_risk: "warning",
  off_track: "danger",
  done: "success",
  no_date: "neutral",
  not_enough_data: "neutral",
};

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "Fri 14 Nov" (with the year when it is not this year), or "—". */
export function dayLabel(iso: string | null | undefined, today = new Date()): string {
  if (!iso) return "—";
  const day = new Date(`${iso}T12:00:00`);
  const label = `${WEEKDAYS[day.getDay()]} ${day.getDate()} ${MONTHS[day.getMonth()]}`;
  return day.getFullYear() === today.getFullYear() ? label : `${label} ${day.getFullYear()}`;
}

/** "Moved twice, 9 days later than first set", or "" when it never moved. */
export function movedLabel(commitment: CommitmentResponse): string {
  if (!commitment.times_moved || !commitment.moved_days) return "";
  const times =
    commitment.times_moved === 1
      ? "once"
      : commitment.times_moved === 2
        ? "twice"
        : `${commitment.times_moved} times`;
  const days = Math.abs(commitment.moved_days);
  const direction = commitment.moved_days > 0 ? "later" : "earlier";
  return `Moved ${times}, ${days} ${days === 1 ? "day" : "days"} ${direction} than first set`;
}

/** Where the target came from, in words: committed, from Jira, or none. */
export function targetSource(scope: ScopeDeliveryResponse): string {
  if (scope.target_source === "committed") return "Committed";
  if (scope.target_source === "jira_release") return "Jira release date";
  return "Not set";
}

/** "85% by Thu 26 Nov · 50% by Fri 20 Nov", or the reason there is no forecast. */
export function historyLabel(scope: ScopeDeliveryResponse, today = new Date()): string {
  const history = scope.history;
  if (history.remaining <= 0) return "All in production";
  if (history.p50 && history.p85) {
    return `85% by ${dayLabel(history.p85, today)} · 50% by ${dayLabel(history.p50, today)}`;
  }
  return history.reason ?? "No forecast yet";
}

/** "Mon 23 Nov (CHK-104) · 3 without a date". */
export function teamLabel(scope: ScopeDeliveryResponse, today = new Date()): string {
  const team = scope.team;
  const parts: string[] = [];
  if (team.latest) parts.push(`${dayLabel(team.latest, today)} (${team.latest_key ?? "?"})`);
  if (team.undated) parts.push(`${team.undated} without a date`);
  return parts.join(" · ") || "No dates yet";
}

export const deliveryKey = (projectId: string, asOf: string) =>
  ["persona", "delivery", projectId, asOf] as const;
