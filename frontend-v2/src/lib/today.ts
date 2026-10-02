/** The date line over a greeting: the day being viewed, today unless given. */
export function todayKicker(programName: string | null, isoDate?: string): string {
  const formatted = dateOf(isoDate)
    .toLocaleDateString("en-US", {
      weekday: "long",
      day: "numeric",
      month: "long",
      year: "numeric",
    })
    .toUpperCase();
  return programName ? `${formatted} · ${programName.toUpperCase()}` : formatted;
}

export function greetingFor(name: string): string {
  const hour = new Date().getHours();
  const part = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  return `${part}, ${firstNameOf(name)}`;
}

/** A greeting uses the given name; the header carries the full one. */
function firstNameOf(name: string): string {
  return name.trim().split(/\s+/)[0] || name;
}

/**
 * Today's date, whatever day the console is viewing.
 *
 * Screens ask for their date with `useViewingDate()` instead, so a past day
 * picked in the header reaches every read.
 */
export function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

/** A YYYY-MM-DD day at local midnight, so it formats as that same day. */
function dateOf(isoDate: string | undefined): Date {
  if (!isoDate) return new Date();
  const [year, month, day] = isoDate.split("-").map(Number);
  return new Date(year, month - 1, day);
}
