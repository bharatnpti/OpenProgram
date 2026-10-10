// Only type imports here: node --test strips types but cannot resolve
// extensionless runtime imports.
import type { ForecastSettingsResponse, ForecastSettingsUpdateRequest } from "../../api/schema";

/*
 * How many working days of history the delivery forecast waits for. The bounds
 * come from the server (`lowest`, `highest`) and the wording mirrors
 * core/domain/forecast.py (validated_min_sample_days), so the form says what
 * the server would refuse before it is sent. The server still decides.
 */

export const MIN_HISTORY_LABEL = "Working days of history before a forecast";

type Bounds = Pick<ForecastSettingsResponse, "lowest" | "highest">;

/** What the field shows for the saved settings: the number in force. */
export function draftFromSettings(settings: Pick<ForecastSettingsResponse, "min_history_days">) {
  return String(settings.min_history_days);
}

/** Why the typed value cannot be saved, in the server's words; null when it can. */
export function minHistoryProblem(text: string, bounds: Bounds): string | null {
  const trimmed = text.trim();
  if (!trimmed) return "Enter a number of working days.";
  if (!/^\d+$/.test(trimmed)) return "Use a whole number of working days, such as 10.";
  const days = Number(trimmed);
  if (days < bounds.lowest) {
    return `A forecast needs at least ${bounds.lowest} working days of history: with fewer, its 50% and 85% dates replay the same one or two days.`;
  }
  if (days > bounds.highest) {
    return `A forecast can wait for at most ${bounds.highest} working days of history, about three months.`;
  }
  return null;
}

/** The request for a typed value that has no problem. */
export function requestFromDraft(text: string): ForecastSettingsUpdateRequest {
  return { min_history_days: Number(text.trim()) };
}

/** Whether the typed value differs from the number in force. */
export function draftChanged(
  text: string,
  settings: Pick<ForecastSettingsResponse, "min_history_days">,
): boolean {
  return text.trim() !== String(settings.min_history_days);
}

/** The help under the field: what the number does, its bounds and the default. */
export function minHistoryHelp(
  settings: Pick<ForecastSettingsResponse, "lowest" | "highest" | "default_min_history_days">,
): string {
  return `The forecast gives its 50% and 85% dates once this many working days of daily snapshots are kept. Fewer give a date sooner but a shakier one; more give a steadier date later. From ${settings.lowest} to ${settings.highest}; the default is ${settings.default_min_history_days}.`;
}

/**
 * Where the number in force comes from, and how far back forecasts read for it:
 * "The default, 10 working days. Forecasts read the last 30 days of snapshots."
 */
export function minHistoryStatus(
  settings: Pick<
    ForecastSettingsResponse,
    "is_default" | "min_history_days" | "default_min_history_days" | "window_days"
  >,
  savedText: string | null,
): string {
  const source = settings.is_default
    ? `The default, ${settings.default_min_history_days} working days; nothing is saved for this tenant.`
    : (savedText ?? "Saved.");
  return `${source} Forecasts read the last ${settings.window_days} days of snapshots.`;
}
