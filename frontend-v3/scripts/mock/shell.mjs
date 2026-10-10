// Mock handlers for the shell lane, used by scripts/mock-api.mjs before the
// console mock: the made-up tenant logo (served by the admin-config mock, which
// owns /config/branding), a member's own check-in schedule,
// the built-in chat with the purposes the backend really sends and its two
// admin actions, and a past day (`as_of`) on the reads it shows most plainly.
// NOT real data: shapes follow src/api/generated.ts, the logo is a made-up mark.
import * as consoleData from "../mock-console.mjs";

// A made-up 64x64 PNG mark drawn for the mock (a white peak on teal); the backend refuses SVG.
const LOGO_PNG =
  "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAABAUlEQVR42u3bUQrCQAyE4bmBN/XR6yu+iKCW7doms8kf8E1Y50taVBppsi63693ppTPLLWwYyqrBD4GoEn4KoVr4XQhVww8hVA+/idAl/E+E1gDdwn8gAAAAAH0BuoZ/IQCQ/AGeBUBXgPcCoBvAt2oDsFUAVAcYKQCqAuypcgAzBUAVgH8KgNUBjqhlAUbDZSKkAsy+dwmAvV3NmgI5dD9zCuQSPgtBbt/1oy8FOXU/Ywrk+Esvcgrk1v3oKZBj+EgEOf/JEXEpyLX7UVMg5/ARZwDgHv7ss+R244s+j+cDAOgOwFNiAAAAQHsA9gUAYGWGpSnW5licZHWW5emO6/MPOnOP230cy8MAAAAASUVORK5CYII=";
const branding = {
  logo: {
    data_url: `data:image/png;base64,${LOGO_PNG}`,
    content_type: "image/png",
    sha256: "mock-logo-1",
    updated_at: "2026-09-28T08:00:00Z",
    updated_by: "U1001",
  },
};

/** The made-up mark, for the admin-config mock, which owns /config/branding. */
export const demoLogo = branding.logo;

// The team's check-in defaults, and what members set for themselves; a field
// not stored follows the team.
const TEAM = {
  local_time: "09:30:00",
  timezone: "Europe/Berlin",
  weekdays: [0, 1, 2, 3, 4],
  reply_wait_seconds: 14400,
  final_reply_wait_seconds: 28800,
};
const FIELDS = Object.keys(TEAM);
const stored = new Map([
  ["U1013", { timezone: "Asia/Tokyo" }],
  ["U1004", { weekdays: [0, 1, 3, 4] }],
]);
const NOT_SELF_SET = {
  reply_wait_seconds:
    "is set by an admin: the reply windows decide when the scrum master and manager hear about a missed check-in",
  final_reply_wait_seconds:
    "is set by an admin: the reply windows decide when the scrum master and manager hear about a missed check-in",
  local_time: "is not set per person: check-ins go out at one time for the whole team",
};

function preference(userId) {
  const own = stored.get(userId) ?? {};
  return {
    developer_id: userId,
    ...Object.fromEntries(FIELDS.map((field) => [field, own[field] ?? TEAM[field]])),
    inherited: FIELDS.filter((field) => own[field] === undefined),
    defaults: TEAM,
    // When the bot asks everyone: one send in UTC, not the team's zone.
    send: consoleData.checkinSend,
  };
}

const refused = (msg, field) => ({ detail: [{ type: "value_error", loc: ["body", field], msg }] });

function validZone(zone) {
  try {
    new Intl.DateTimeFormat("en", { timeZone: zone });
    return true;
  } catch {
    return false;
  }
}

/** The backend's PUT /me/checkin-preference rules: only days and zone, null resets. */
function updatePreference(userId, body) {
  if (!body || typeof body !== "object") return [422, refused("Input should be an object", "")];
  const extra = Object.keys(body).filter((key) => key !== "timezone" && key !== "weekdays");
  if (extra.length > 0) {
    const reasons = extra.map((key) => `${key} ${NOT_SELF_SET[key] ?? "is not allowed"}`);
    return [422, refused(`Value error, ${reasons.join("; ")}`, extra[0])];
  }
  const own = { ...(stored.get(userId) ?? {}) };
  if ("weekdays" in body) {
    const days = body.weekdays;
    if (days !== null && (!Array.isArray(days) || days.length === 0)) {
      return [
        422,
        refused(
          "Value error, weekdays needs at least one day: with none, this person is never asked to check in",
          "weekdays",
        ),
      ];
    }
    if (days !== null && days.some((day) => !Number.isInteger(day) || day < 0 || day > 6)) {
      return [422, refused("Value error, weekdays must be in the range 0..6", "weekdays")];
    }
    if (days === null) delete own.weekdays;
    else own.weekdays = [...days];
  }
  if ("timezone" in body) {
    if (body.timezone !== null && !validZone(body.timezone)) {
      return [422, refused("Value error, timezone must be a valid IANA timezone", "timezone")];
    }
    if (body.timezone === null) delete own.timezone;
    else own.timezone = body.timezone;
  }
  stored.set(userId, own);
  return [200, preference(userId)];
}

// The purposes the backend sends (status_collector.py, cross_person_service.py).
const REAL_PURPOSE = {
  checkin: "status_checkin",
  followup: "status_clarification",
  nudge: "status_nudge",
};

function chatMessages(userId) {
  const { items } = consoleData.chatMessages(userId);
  const real = items.map((item) => ({
    ...item,
    purpose: item.purpose ? (REAL_PURPOSE[item.purpose] ?? item.purpose) : null,
  }));
  const extra = (id, text, created_at, purpose, metadata = {}) => ({
    ...real[0],
    message_id: id,
    text,
    created_at,
    purpose,
    metadata,
  });
  return {
    items: [
      ...real,
      extra(
        "m2a",
        "Thanks, noted: handlers done, capture path next, and a second reviewer needed on MR !214.",
        "2026-10-05T07:21:00Z",
        "status_ack",
      ),
      extra(
        "m6",
        "Just checking in again: is the capture path merged, and do you still need a reviewer?",
        "2026-10-06T11:00:00Z",
        "status_nudge",
      ),
    ],
  };
}

function readJson(req) {
  return new Promise((resolve) => {
    let raw = "";
    req.on("data", (chunk) => (raw += chunk));
    req.on("end", () => {
      try {
        resolve(raw ? JSON.parse(raw) : {});
      } catch {
        resolve(undefined);
      }
    });
  });
}

/** Past-day versions of two reads, so the header's day picker visibly changes them. */
function pastDay(p, userId, day, roles) {
  let m;
  if ((m = p.match(/^\/pods\/([^/]+)\/checkins$/))) {
    if (!roles.some((r) => ["sm", "mgr", "admin"].includes(r))) return null;
    const today = consoleData.podCheckins(m[1]);
    const developers = today.developers.map((dev) => ({
      ...dev,
      state: "confirmed",
      source: "confirmed",
      status_as_of: day,
      summary: dev.summary || "Answered that day",
    }));
    return {
      ...today,
      as_of: day,
      confirmed: developers.length,
      partial: 0,
      stale: 0,
      missing: 0,
      developers,
    };
  }
  if (p === "/me/status") {
    return {
      ...consoleData.myStatus(userId),
      source: "confirmed",
      developer_confirmed: true,
      status_as_of: day,
      confirmed_at: `${day}T07:20:00Z`,
    };
  }
  return null;
}

/** Returns true when it answered the request (now, or once its body is read). */
export function api(req, url, roles, userId, send, deny) {
  const p = url.pathname;
  const day = url.searchParams.get("as_of");
  const reply = (status, body) => {
    send(status, body);
    return true;
  };
  const refuse = () => {
    deny();
    return true;
  };
  const afterBody = (handle) => {
    void readJson(req).then(handle);
    return true;
  };

  if (p === "/me/checkin-preference") {
    if (!consoleData.roster.some((person) => person.id === userId)) {
      return reply(404, {
        detail: "check-in preference is not available: no member record for this person",
      });
    }
    if (req.method === "GET") return reply(200, preference(userId));
    if (req.method === "PUT") return afterBody((body) => send(...updatePreference(userId, body)));
  }

  if (p === "/test/chat-simulator/messages") {
    return reply(200, chatMessages(url.searchParams.get("user_id") ?? userId));
  }
  if (p === "/admin/workflows/checkin/dispatch" && req.method === "POST") {
    if (!roles.includes("admin")) return refuse();
    return afterBody((body) => {
      // The console sends only the tenant and the member: chat_external_id
      // would override where the DM goes.
      if (body && ("chat_external_id" in body || "developer_name" in body)) {
        console.warn("mock: check-in dispatch carried more than tenant_id and developer_id", body);
      }
      send(200, { workflow_id: `checkin-${body?.developer_id ?? "unknown"}-mock` });
    });
  }
  if (p === "/test/chat-simulator/state" && req.method === "DELETE") {
    return roles.includes("admin") ? reply(204, null) : refuse();
  }

  if (day && req.method === "GET") {
    const body = pastDay(p, userId, day, roles);
    if (body) return reply(200, body);
  }
  return false;
}
