/**
 * Check-in reply windows are whole seconds. Admins edit them as an amount and a
 * unit, and these helpers convert between the two without rounding or clamping,
 * so a stored value always comes back unchanged.
 */

/** Longest window the backend stores: a Postgres INTEGER of seconds. */
export const MAX_WAIT_SECONDS = 2_147_483_647;

export type DurationUnit = "h" | "min" | "s";

/** Largest unit first, so `splitDuration` picks the shortest exact reading. */
export const durationUnits: ReadonlyArray<{ unit: DurationUnit; label: string; seconds: number }> =
  [
    { unit: "h", label: "hours", seconds: 3600 },
    { unit: "min", label: "minutes", seconds: 60 },
    { unit: "s", label: "seconds", seconds: 1 },
  ];

export type DurationParts = { amount: string; unit: DurationUnit };

export type ParsedDuration = { ok: true; seconds: number } | { ok: false; error: string };

/** Seconds in the largest unit that holds them exactly: 14400 is 4 h, 5400 is 90 min. */
export function splitDuration(seconds: number): DurationParts {
  if (seconds > 0) {
    const fit = durationUnits.find((option) => seconds % option.seconds === 0);
    if (fit) {
      return { amount: String(seconds / fit.seconds), unit: fit.unit };
    }
  }
  return { amount: String(seconds), unit: "s" };
}

/** The seconds an amount and unit stand for, or why they can't be saved. */
export function parseDuration(parts: DurationParts, max = MAX_WAIT_SECONDS): ParsedDuration {
  const amount = parts.amount.trim();
  if (amount === "") {
    return { ok: false, error: "Enter a duration." };
  }
  if (!/^\d+$/.test(amount)) {
    return { ok: false, error: "Enter a whole number." };
  }
  const unitSeconds = durationUnits.find((option) => option.unit === parts.unit)?.seconds ?? 1;
  const seconds = Number(amount) * unitSeconds;
  if (seconds > max) {
    return { ok: false, error: "That is too long to save." };
  }
  return { ok: true, seconds };
}

/** A window as people say it: "4 h", "90 min", "61 s". */
export function formatDuration(seconds: number): string {
  const { amount, unit } = splitDuration(seconds);
  return `${amount} ${unit}`;
}
