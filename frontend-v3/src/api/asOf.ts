// The day reads are asked for. No runtime imports, so `node --test` can run it.

/**
 * The past day the console is viewing, or null for today.
 *
 * Every read that accepts `as_of` gets it here, in the request layer, rather
 * than each screen passing a date: a screen reads the day being viewed without
 * knowing about it, whoever writes it. Installed by the viewing-date provider
 * during render, like the acting-as identity, so the first request a newly
 * shown screen makes already carries it.
 */
let viewingAsOf: string | null = null;

export function setViewingAsOf(day: string | null): void {
  viewingAsOf = day;
}

export function currentViewingAsOf(): string | null {
  return viewingAsOf;
}

/**
 * The GET paths that accept `as_of`, as the OpenAPI document names them.
 * `asOf.test.ts` compares this list with `openapi.json`, so a backend change
 * that adds or drops `as_of` fails the tests until the list follows.
 */
export const DATED_READS = [
  "/graph/programs/{program_id}/tree",
  "/me/focus",
  "/me/status",
  "/persona/{level}/{entity_id}/trend",
  "/pods",
  "/pods/{pod_id}/blockers",
  "/pods/{pod_id}/checkins",
  "/pods/{pod_id}/delivery",
  "/pods/{pod_id}/rollup",
  "/pods/{pod_id}/tasks",
  "/portfolio/attention",
  "/portfolio/flow",
  "/portfolio/heatmap",
  "/portfolio/risks",
  "/programs",
  "/programs/{program_id}/tree",
  "/projects",
  "/projects/{project_id}/delivery",
  "/projects/{project_id}/gates",
  "/projects/{project_id}/progress",
  "/projects/{project_id}/requirements",
  "/projects/{project_id}/risks",
  "/projects/{project_id}/workstreams",
  "/workstreams",
  "/workstreams/{workstream_id}",
  "/workstreams/{workstream_id}/flow",
  "/workstreams/{workstream_id}/progress",
] as const;

const DATED_PATTERNS = DATED_READS.map(
  (template) => new RegExp(`^${template.replace(/\{[^}]+\}/g, "[^/]+")}$`),
);

/** Whether the backend reads this path (without its query) for a given day. */
export function acceptsAsOf(pathname: string): boolean {
  return DATED_PATTERNS.some((pattern) => pattern.test(pathname));
}

/**
 * `path` asking for `day`, when it is a read that accepts `as_of` and does not
 * name a day already. Changes are never dated here: a change made while a past
 * day is viewed is refused before it is sent (see `setReadOnlyReason`).
 */
export function withViewingAsOf(
  path: string,
  method: string | undefined,
  day: string | null = viewingAsOf,
): string {
  if (!day || (method ?? "GET").toUpperCase() !== "GET") return path;
  const queryAt = path.indexOf("?");
  const pathname = queryAt === -1 ? path : path.slice(0, queryAt);
  const query = queryAt === -1 ? "" : path.slice(queryAt + 1);
  if (!acceptsAsOf(pathname) || new URLSearchParams(query).has("as_of")) return path;
  return `${path}${query ? "&" : "?"}as_of=${encodeURIComponent(day)}`;
}

/**
 * The day the backend reads as today, or null until it has said. A dated read
 * sent without `as_of` answers for the server's `date.today()` and echoes that
 * day, so the console learns it from the answers it gets anyway. Counting by
 * it keeps the console and the API on one calendar: a UTC browser and an IST
 * server otherwise disagree from 00:00 to 05:30, and the day before cannot be
 * picked.
 */
let serverToday: string | null = null;
const serverTodayListeners = new Set<() => void>();
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

export function currentServerToday(): string | null {
  return serverToday;
}

export function subscribeServerToday(listener: () => void): () => void {
  serverTodayListeners.add(listener);
  return () => {
    serverTodayListeners.delete(listener);
  };
}

/**
 * Called with each successful answer and the path it was sent to. Only a read
 * that accepts `as_of` and was sent without one teaches today; an answer for a
 * past day, a change, or a body without a well-formed `as_of` is ignored.
 */
export function learnServerToday(
  sentPath: string,
  method: string | undefined,
  body: unknown,
): void {
  if ((method ?? "GET").toUpperCase() !== "GET") return;
  const queryAt = sentPath.indexOf("?");
  const pathname = queryAt === -1 ? sentPath : sentPath.slice(0, queryAt);
  const query = queryAt === -1 ? "" : sentPath.slice(queryAt + 1);
  if (!acceptsAsOf(pathname) || new URLSearchParams(query).has("as_of")) return;
  const day =
    typeof body === "object" && body !== null ? (body as { as_of?: unknown }).as_of : undefined;
  if (typeof day !== "string" || !ISO_DAY.test(day) || day === serverToday) return;
  serverToday = day;
  for (const listener of serverTodayListeners) listener();
}
