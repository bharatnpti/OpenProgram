export function todayKicker(programName: string | null): string {
  const formatted = new Date()
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

export function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}
