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

/**
 * What went wrong, in the server's words. FastAPI answers a request it cannot
 * read with a list of problems, which the API client only counts; this reads
 * each one, so a refused form says why instead of "status 422".
 */
export function errorText(error: unknown): string {
  if (typeof error !== "object" || error === null) return "Something went wrong.";
  const body = (error as { detail?: unknown }).detail;
  const detail =
    typeof body === "object" && body !== null ? (body as { detail?: unknown }).detail : undefined;
  if (Array.isArray(detail) && detail.length > 0) {
    const lines = detail.map((item) => problemLine(item)).filter(Boolean);
    if (lines.length > 0) return [...new Set(lines)].join(" ");
  }
  const message = (error as { message?: unknown }).message;
  return typeof message === "string" && message ? message : "Something went wrong.";
}

function problemLine(item: unknown): string {
  if (typeof item !== "object" || item === null) return "";
  const { msg, loc } = item as { msg?: unknown; loc?: unknown };
  if (typeof msg !== "string" || !msg) return "";
  const own = msg.startsWith("Value error, ") ? msg.slice("Value error, ".length) : null;
  const field = Array.isArray(loc)
    ? [...loc].reverse().find((part) => typeof part === "string" && part !== "body")
    : undefined;
  const text = own ?? (field ? `${String(field).replace(/_/g, " ")}: ${msg}` : msg);
  const sentence = text.charAt(0).toUpperCase() + text.slice(1);
  return /[.!?]$/.test(sentence) ? sentence : `${sentence}.`;
}

// ---- Sync schedules -------------------------------------------------------------

type CronFields = { minutes: Set<number>; hours: Set<number>; days: Set<number> } | null;

/**
 * The minute, hour and weekday a cron runs at. Only the shapes sync schedules
 * use are read: any day of the month in any month. A six-field cron's leading
 * seconds are ignored. Anything else is null, and is shown as written.
 */
function cronFields(cron: string | null | undefined): CronFields {
  const parts = (cron ?? "").trim().split(/\s+/);
  const fields = parts.length === 6 ? parts.slice(1) : parts;
  if (fields.length !== 5) return null;
  const [minute, hour, dayOfMonth, month, dayOfWeek] = fields;
  if (dayOfMonth !== "*" || month !== "*") return null;
  const minutes = cronSet(minute, 0, 59);
  const hours = cronSet(hour, 0, 23);
  const days = cronSet(dayOfWeek.replace(/\b7\b/g, "0"), 0, 6);
  if (!minutes || !hours || !days) return null;
  return { minutes, hours, days };
}

function cronSet(field: string, low: number, high: number): Set<number> | null {
  const values = new Set<number>();
  for (const part of field.split(",")) {
    const [base, stepText] = part.split("/");
    const step = stepText === undefined ? 1 : Number(stepText);
    if (!Number.isInteger(step) || step < 1) return null;
    let from = low;
    let to = high;
    if (base !== "*") {
      const [start, end] = base.split("-");
      if (!/^\d+$/.test(start) || (end !== undefined && !/^\d+$/.test(end))) return null;
      from = Number(start);
      to = end === undefined ? (stepText === undefined ? from : high) : Number(end);
    }
    if (from < low || to > high || from > to) return null;
    for (let value = from; value <= to; value += step) values.add(value);
  }
  return values;
}

const two = (value: number) => String(value).padStart(2, "0");

/** "every 15 minutes", "every hour", "every 6 hours", "once a day at 06:00 UTC". */
export function cronLabel(cron: string | null | undefined): string {
  const fields = cronFields(cron);
  if (!fields) return cron ? `on the schedule ${cron}` : "on no schedule";
  const { minutes, hours, days } = fields;
  const everyDay = days.size === 7;
  const minuteList = [...minutes].sort((a, b) => a - b);
  const hourList = [...hours].sort((a, b) => a - b);
  if (everyDay && hours.size === 24 && minutes.size > 1) {
    const gap = minuteList[1] - minuteList[0];
    const even = minuteList.every(
      (value, index) => index === 0 || value - minuteList[index - 1] === gap,
    );
    if (even && gap * minutes.size === 60)
      return gap === 1 ? "every minute" : `every ${gap} minutes`;
  }
  if (everyDay && minutes.size === 1 && hours.size === 24) return "every hour";
  if (everyDay && minutes.size === 1 && hours.size > 1) {
    const gap = hourList[1] - hourList[0];
    const even = hourList.every(
      (value, index) => index === 0 || value - hourList[index - 1] === gap,
    );
    if (even && gap * hours.size === 24) return `every ${gap} hours`;
  }
  if (minutes.size === 1 && hours.size === 1) {
    const at = `${two(hourList[0])}:${two(minuteList[0])} UTC`;
    if (everyDay) return `once a day at ${at}`;
    if (days.size === 5 && [1, 2, 3, 4, 5].every((day) => days.has(day))) {
      return `on weekdays at ${at}`;
    }
  }
  return `on the schedule ${cron}`;
}

/**
 * When a sync schedule next runs after `from`. Sync schedules are read in UTC
 * (they carry no time zone of their own); null when the cron is not one of the
 * shapes `cronFields` reads, or when it would not run within a week.
 */
export function nextCronRun(cron: string | null | undefined, from: Date): Date | null {
  const fields = cronFields(cron);
  if (!fields) return null;
  const next = new Date(from.getTime());
  next.setUTCSeconds(0, 0);
  for (let step = 0; step < 8 * 24 * 60; step += 1) {
    next.setUTCMinutes(next.getUTCMinutes() + 1);
    if (
      fields.minutes.has(next.getUTCMinutes()) &&
      fields.hours.has(next.getUTCHours()) &&
      fields.days.has(next.getUTCDay())
    ) {
      return next;
    }
  }
  return null;
}

// ---- Sync targets ---------------------------------------------------------------

const PROVIDER_NAMES: Record<string, string> = {
  jira: "Jira",
  github: "GitHub",
  gitlab: "GitLab",
  slack: "Slack",
  google: "Google Calendar",
  google_calendar: "Google Calendar",
};

/** The system a source reads, by the name people know it by; a stand-in says so. */
export function providerLabel(provider: string, simulated: boolean): string {
  if (simulated) return "simulated";
  return PROVIDER_NAMES[provider] ?? provider;
}

const KIND_WORDS: Record<string, string> = {
  project: "Project",
  pod: "Pod",
  program: "Program",
  workstream: "Workstream",
};

/**
 * How a sync target reads: its name, what kind of target it is, and what it
 * covers. The status names a target "<Kind> <name>" and keeps the kind in its
 * scope, so the kind is said once instead of "Pod Payments Pod".
 */
export function targetParts(target: { scope: string; label: string; detail?: string | null }): {
  name: string;
  kind: string;
  detail: string | null;
} {
  const { scope, label } = target;
  const detail = target.detail ?? null;
  const withoutPrefix = (word: string) =>
    label.startsWith(`${word} `) ? label.slice(word.length + 1) : label;
  const [prefix, rest = ""] = splitOnce(scope, ":");
  if (prefix === "query") {
    const [kind] = splitOnce(rest, ":");
    const word = KIND_WORDS[kind] ?? (kind ? kind.charAt(0).toUpperCase() + kind.slice(1) : "");
    return {
      name: word ? withoutPrefix(word) : label,
      kind: word ? `${word}, by saved query` : "Saved query",
      detail,
    };
  }
  if (prefix === "project" && rest) {
    const name = withoutPrefix("Project");
    return { name, kind: "Jira project", detail: name === rest ? detail : `Key ${rest}` };
  }
  if (prefix === "repo") return { name: label, kind: "Repository", detail };
  if (scope === "workspace") return { name: label, kind: "Workspace", detail };
  return { name: label, kind: "", detail };
}

function splitOnce(text: string, separator: string): [string, string | undefined] {
  const at = text.indexOf(separator);
  return at < 0 ? [text, undefined] : [text.slice(0, at), text.slice(at + separator.length)];
}

/**
 * "Wed 7 Oct 00:07" for a timestamp, date and time both in the viewer's own
 * time. (lib/format's formatDay reads an ISO day; on a timestamp it would give
 * the UTC date beside a local time, a day off around midnight.)
 */
export function stampLabel(iso: string | null | undefined): string {
  if (!iso) return "never";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  const day = date.toLocaleDateString("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
  });
  const time = date.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  return `${day} ${time}`;
}

/** "1 person", "14 people". */
export function peopleCount(count: number): string {
  return `${count} ${count === 1 ? "person" : "people"}`;
}
