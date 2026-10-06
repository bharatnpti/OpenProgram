// Pure wording for the admin screens, no imports so `node --test` can run it.

/** "2 h", "90 min", "1 day" for a wait in seconds. */
export function minutesLabel(seconds: number): string {
  const minutes = Math.round(seconds / 60);
  if (minutes >= 1440 && minutes % 1440 === 0) {
    const days = minutes / 1440;
    return `${days} ${days === 1 ? "day" : "days"}`;
  }
  if (minutes >= 60 && minutes % 60 === 0) return `${minutes / 60} h`;
  return `${minutes} min`;
}

/** A stable id for a new entity: "project-checkout-revamp". */
export function slugId(kind: string, name: string): string {
  const slug = name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return `${kind}-${slug || "new"}`;
}
