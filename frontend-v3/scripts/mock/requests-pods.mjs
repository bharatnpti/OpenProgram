// Mock handlers for Requests and Pods, used by scripts/mock-api.mjs
// before the other mocks: a request's trail, the board as counts, the asks
// resolved lately, a pod's check-ins with the day's silence ladder, and a
// project's check-in counts. NOT real data: made-up demo shapes that follow
// src/api/generated.ts, on the console mock's roster and requests.
import * as consoleData from "../mock-console.mjs";

const has = (roles, ...wanted) => roles.some((role) => wanted.includes(role));
const NAME_READERS = ["admin", "mgr", "po", "sm"];
const AGGREGATE = [...NAME_READERS, "exec"];
const DAY = "2026-10-06";

const nameOf = (id) => consoleData.roster.find((person) => person.id === id)?.name ?? null;

const resolved = [
  {
    id: "r9",
    requester_id: "U1002",
    counterpart_id: "U1004",
    counterpart_display_name: nameOf("U1004"),
    counterpart_email: null,
    kind: "review",
    status: "resolved",
    note: "Second review on the refund rounding change (CHK-102)",
    raw_name: null,
    source_correlation_id: "c-r9",
    created_at: "2026-09-29T08:00:00Z",
    updated_at: "2026-10-02T11:20:00Z",
    delivery: "sent",
  },
];

const step = (over) => ({
  kind: "asked",
  state: "done",
  at: null,
  on: null,
  who: null,
  role: null,
  by: null,
  dm: null,
  dm_attempts: 0,
  dm_at: null,
  eta: null,
  project_name: null,
  report: null,
  computed: false,
  ...over,
});

/** A trail for each request: what its status and delivery make true, nothing more. */
function trailOf(request, hide) {
  const who = hide ? null : request.counterpart_display_name;
  const named = (name) => (hide ? null : name);
  const steps = [];
  if (request.status === "needs_resolution") {
    steps.push(
      step({ kind: "unmatched", at: request.created_at, who: hide ? null : "the payments lead" }),
    );
  } else {
    const dm =
      request.delivery === "sent"
        ? "sent"
        : request.delivery === "retrying"
          ? "retrying"
          : request.delivery === "not_delivered"
            ? "not_delivered"
            : "none";
    steps.push(
      step({
        at: request.created_at,
        who,
        dm,
        dm_attempts: dm === "sent" ? 1 : dm === "none" ? 0 : 3,
        dm_at: dm === "sent" ? request.created_at.replace("08:00", "08:01") : null,
      }),
    );
  }
  if (request.status === "acknowledged" || request.status === "resolved") {
    steps.push(
      step({
        kind: "acknowledged",
        at: request.created_at.replace("T08:00", "T13:00"),
        by: who,
        eta: "by Thursday",
      }),
    );
  }
  if (request.status !== "needs_resolution") {
    steps.push(
      step({
        kind: "named",
        state: request.status === "resolved" ? "done" : "due",
        on: request.status === "resolved" ? "2026-10-01" : "2026-10-07",
        who: named("Ira Novak"),
        role: "Scrum master",
        project_name: "Checkout Revamp",
        report: request.status === "resolved" ? "sent" : "upcoming",
        computed: true,
      }),
    );
  }
  if (request.status === "resolved") {
    steps.push(step({ kind: "resolved", at: request.updated_at, by: who }));
  }
  return steps;
}

/** The pod board with each person's day as the ladder saw it, for its readers. */
function checkinsWithLadder(podId, roles, userId) {
  const board = consoleData.podCheckins(podId);
  const pod = consoleData.pods.find((item) => item.id === podId);
  const ownPod = Boolean(pod && pod.member_ids.includes(userId));
  const schedule = has(roles, "admin") || (has(roles, "sm") && ownPod);
  const at = (time) => `${DAY}T${time}:00Z`;
  const developers = board.developers.map((dev) => {
    const silent = dev.state === "missing" || dev.state === "stale";
    return {
      ...dev,
      asked_at: at("07:30"),
      replied_at: silent ? null : dev.state === "partial" ? at("10:12") : at("07:41"),
      reminders: silent
        ? [
            { number: 1, target: "developer", sent_at: at("11:30"), recorded: true },
            { number: 2, target: "scrum_master", sent_at: at("15:30"), recorded: true },
          ]
        : [],
      checkin_time: schedule ? "09:30" : null,
      timezone: schedule ? "Europe/Berlin" : null,
    };
  });
  return { ...board, developers, reads_ladder: true, reads_schedule: schedule };
}

export function api(req, url, roles, userId, send, deny) {
  const p = url.pathname;
  const reply = (status, body) => {
    send(status, body);
    return true;
  };
  const refuse = () => {
    deny();
    return true;
  };
  let m;
  if (req.method !== "GET") return false;
  if (
    p === "/portfolio/cross-person-requests" &&
    has(roles, "exec") &&
    !has(roles, ...NAME_READERS)
  )
    return reply(403, {
      detail: "An executive reads the requests as counts: every ask names who is waiting on whom.",
    });
  if (p === "/cross-person-requests/counts") {
    if (!has(roles, ...AGGREGATE)) return refuse();
    const active = consoleData.requests;
    const count = (status) => active.filter((r) => r.status === status).length;
    return reply(200, {
      as_of: DAY,
      open: count("open"),
      acknowledged: count("acknowledged"),
      needs_resolution: count("needs_resolution"),
      waiting_over_a_week: active.filter(
        (r) => (r.status === "open" || r.status === "acknowledged") && r.created_at < "2026-09-30",
      ).length,
      not_delivered: active.filter((r) => r.status === "open" && r.delivery === "not_delivered")
        .length,
      resolved_recently: resolved.length,
      resolved_window_days: 30,
    });
  }
  if (p === "/cross-person-requests/resolved") {
    if (!has(roles, ...NAME_READERS)) return refuse();
    return reply(200, { requests: resolved });
  }
  if ((m = p.match(/^\/cross-person-requests\/([^/]+)\/trail$/))) {
    const request = [...consoleData.requests, ...resolved].find((r) => r.id === m[1]);
    if (!request) return reply(404, { detail: `cross-person request ${m[1]} not found` });
    const involved = [request.requester_id, request.counterpart_id].includes(userId);
    if (!involved && !has(roles, ...AGGREGATE)) return refuse();
    const hide = !involved && !has(roles, ...NAME_READERS);
    return reply(200, {
      request_id: request.id,
      status: request.status,
      as_of: DAY,
      names_hidden: hide,
      steps: trailOf(request, hide),
    });
  }
  if ((m = p.match(/^\/pods\/([^/]+)\/checkins$/)) && !url.searchParams.get("as_of")) {
    // The ladder's readers; a developer's own pod is the scoped-delivery mock's.
    if (!has(roles, "sm", "mgr", "admin")) return false;
    return reply(200, checkinsWithLadder(m[1], roles, userId));
  }
  if ((m = p.match(/^\/projects\/([^/]+)\/checkin-coverage$/))) {
    if (!has(roles, "po", "mgr", "exec", "admin", "sm")) return refuse();
    return reply(200, {
      project_id: m[1],
      as_of: DAY,
      people: 9,
      coverage: { replied: 5, inferred: 2, no_data: 1, waiting: 1 },
    });
  }
  return false;
}
