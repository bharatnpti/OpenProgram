// Mock handlers for the forecast-min-history lane: how many working days of
// history the delivery forecast waits for (GET and PUT /config/delivery/forecast,
// admin only). NOT real data: shapes follow src/api/generated.ts. Wired from
// ../mock-api.mjs before the admin-config mock, which answers the rest of
// /config/delivery/.
//
// The other mocks read `neededDays()`, so their forecasts' `needed_days`, the
// "a forecast needs N" reasons and the requirements read's
// `forecast_needed_days` follow what is saved here. Their made-up history stays
// as it is: a minimum above it does not take a mock forecast away.

const DEFAULT = 10;
const LOWEST = 3;
const HIGHEST = 60;

/** What an admin saved: { days, at, by }, or null while the default applies. */
let saved = null;

/** The working days of history a forecast needs in force: the saved number, else the default. */
export function neededDays() {
  return saved?.days ?? DEFAULT;
}

/** core/domain/forecast.py `history_window_days`: 30, or more when the minimum needs it. */
function windowDays(minimum) {
  const fewest = (days) => Math.floor(days / 7) * 5 + Math.max(0, (days % 7) - 2);
  let window = 30;
  while (fewest(window) < minimum + 1) window += 1;
  return window;
}

/** The requirements read's timeline when its days are left to the server. */
export function timelineDays() {
  return Math.max(30, windowDays(neededDays()));
}

function response() {
  const days = neededDays();
  return {
    min_history_days: days,
    default_min_history_days: DEFAULT,
    is_default: saved === null,
    lowest: LOWEST,
    highest: HIGHEST,
    window_days: windowDays(days),
    updated_at: saved?.at ?? null,
    updated_by: saved?.by ?? null,
  };
}

/** The server's words for a number it refuses (validated_min_sample_days). */
function problem(days) {
  if (!Number.isInteger(days)) return "min_history_days: Input should be a valid integer";
  if (days < LOWEST) {
    return `A forecast needs at least ${LOWEST} working days of history: with fewer, its 50% and 85% dates replay the same one or two days.`;
  }
  if (days > HIGHEST) {
    return `A forecast can wait for at most ${HIGHEST} working days of history, about three months.`;
  }
  return null;
}

function readJson(req) {
  return new Promise((resolve) => {
    let raw = "";
    req.on("data", (chunk) => (raw += chunk));
    req.on("end", () => {
      try {
        resolve(JSON.parse(raw || "{}"));
      } catch {
        resolve(undefined);
      }
    });
  });
}

/** Answers this lane's endpoint and returns true, or returns false for the next handler. */
export function api(req, url, roles, actingAs, send, deny) {
  if (url.pathname !== "/config/delivery/forecast") return false;
  if (!roles.includes("admin")) {
    deny();
    return true;
  }
  const method = req.method ?? "GET";
  if (method === "GET") {
    send(200, response());
    return true;
  }
  if (method !== "PUT") {
    send(405, { detail: "Method Not Allowed" });
    return true;
  }
  readJson(req).then((body) => {
    if (body === undefined) return send(422, { detail: "The request body is not JSON." });
    const days = body.min_history_days;
    if (days === null) {
      saved = null;
      return send(200, response());
    }
    const refused = problem(days);
    if (refused) return send(422, { detail: refused });
    saved = { days, at: new Date().toISOString(), by: actingAs ?? "U1001" };
    return send(200, response());
  });
  return true;
}
