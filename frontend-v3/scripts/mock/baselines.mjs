// Mock handlers for the baselines lane: planning baselines (GET and POST
// /projects/{id}/baselines). NOT real data: shapes follow src/api/generated.ts,
// names follow the seeded demo tenant. Checkout Revamp starts with one baseline,
// set by the product owner on 15 Sep, with three requirements added since (one
// labelled CR); Release 1.0 has none, so its section offers Mark planning
// complete. A new baseline replaces the active one and keeps it, as the backend
// does, while the mock runs.
//
// Reading needs read_project_progress (product owner, manager, executive,
// admin); marking planning complete needs set_project_dates (product owner,
// manager, admin). Wired from ../mock-api.mjs before the lane mocks.
import { roster } from "../mock-console.mjs";

const TODAY = "2026-10-06";
const PROJECT = "project-checkout";
const RELEASE = "rel-1-0";
const NAMES = { [PROJECT]: "Checkout Revamp", [RELEASE]: "Release 1.0" };
/** Requirements in scope today, as the reports mock counts them. */
const IN_SCOPE = { [PROJECT]: 24, [RELEASE]: 7 };
const nameOf = (id) => roster.find((p) => p.id === id)?.name ?? "a team member";

const since = (added = [], labelled = [], removed = []) => ({
  added: added.length,
  added_with_label: labelled.length,
  removed: removed.length,
  added_keys: added,
  added_with_label_keys: labelled,
  removed_keys: removed,
});

/** Each scope's baselines, oldest first. */
const kept = {
  [PROJECT]: [
    {
      baseline_id: "bl-1",
      number: 1,
      active: true,
      scope_kind: "project",
      scope_id: PROJECT,
      project_id: PROJECT,
      kind: "snapshot",
      planned_on: "2026-09-15",
      reconstructed: false,
      snapshot_day: null,
      requirements: 21,
      open: 17,
      points: null,
      change_label: "CR",
      grace_working_days: 2,
      note: "Scope agreed at the planning review",
      set_by: "U1003",
      set_by_name: nameOf("U1003"),
      set_at: "2026-09-15T14:00:00Z",
      superseded_at: null,
      since: since(["CHK-109", "CHK-111", "CHK-112"], ["CHK-109"]),
    },
  ],
  [RELEASE]: [],
};

function readBody(req) {
  return new Promise((resolve) => {
    let data = "";
    req.on("data", (chunk) => (data += chunk));
    req.on("end", () => {
      try {
        resolve(data ? JSON.parse(data) : {});
      } catch {
        resolve({});
      }
    });
  });
}

function read(scopeId) {
  const listed = [...(kept[scopeId] ?? [])].reverse();
  return {
    project_id: PROJECT,
    release_id: scopeId === PROJECT ? null : scopeId,
    scope_kind: scopeId === PROJECT ? "project" : "release",
    scope_id: scopeId,
    name: NAMES[scopeId],
    as_of: TODAY,
    in_scope: IN_SCOPE[scopeId],
    active: listed.find((item) => item.active) ?? null,
    baselines: listed,
    default_change_label: "CR",
    default_grace_working_days: 2,
  };
}

/** core/domain/baseline.py's checks, in the backend's words. */
function problem(body) {
  const label = typeof body.change_label === "string" ? body.change_label.trim() : "";
  if (/\s/.test(label)) return "A change label is one Jira label, with no spaces.";
  const grace = body.grace_working_days ?? 2;
  if (!Number.isInteger(grace) || grace < 0 || grace > 20) {
    return "The grace period is 0 to 20 working days after planning.";
  }
  if (body.planned_on && body.planned_on > TODAY) {
    return "Planning can be marked complete today or on an earlier day.";
  }
  return null;
}

/** Handles the call and returns true, or returns false for the next handler. */
// eslint-disable-next-line no-unused-vars -- `deny` is part of the lane handler signature.
export function api(req, url, roles, userId, send, deny) {
  const m = url.pathname.match(/^\/projects\/([^/]+)\/baselines$/);
  if (!m) return false;
  const method = req.method ?? "GET";
  const has = (...r) => roles.includes("admin") || roles.some((x) => r.includes(x));
  const reply = (status, body) => {
    send(status, body);
    return true;
  };
  if (m[1] !== PROJECT) {
    if (method === "GET" && has("po", "mgr", "exec")) {
      return reply(200, {
        ...read(PROJECT),
        project_id: m[1],
        scope_id: m[1],
        name: m[1],
        in_scope: 0,
        active: null,
        baselines: [],
      });
    }
    return false;
  }

  if (method === "GET") {
    if (!has("po", "mgr", "exec"))
      return reply(403, { detail: `${userId} is not authorized for read_project_progress` });
    const scopeId = url.searchParams.get("release_id") || PROJECT;
    if (!(scopeId in kept)) return reply(404, { detail: `No release '${scopeId}'.` });
    return reply(200, read(scopeId));
  }
  if (method === "POST") {
    if (!has("po", "mgr"))
      return reply(403, { detail: `${userId} is not authorized for set_project_dates` });
    void readBody(req).then((body) => {
      const scopeId = body.release_id || PROJECT;
      if (!(scopeId in kept)) return send(404, { detail: `No release '${scopeId}'.` });
      const wrong = problem(body);
      if (wrong) return send(422, { detail: wrong });
      const listed = kept[scopeId];
      const at = new Date().toISOString();
      for (const item of listed) {
        if (item.active) Object.assign(item, { active: false, superseded_at: at });
      }
      const label = typeof body.change_label === "string" && body.change_label.trim();
      const earlier = Boolean(body.planned_on && body.planned_on < TODAY);
      const baseline = {
        baseline_id: `bl-${scopeId}-${listed.length + 1}`,
        number: listed.length + 1,
        active: true,
        scope_kind: scopeId === PROJECT ? "project" : "release",
        scope_id: scopeId,
        project_id: PROJECT,
        kind: earlier ? "date" : "snapshot",
        planned_on: body.planned_on || TODAY,
        reconstructed: false,
        snapshot_day: null,
        requirements: IN_SCOPE[scopeId],
        open: scopeId === PROJECT ? 17 : 5,
        points: null,
        change_label: label || "CR",
        grace_working_days: body.grace_working_days ?? 2,
        note: (body.note ?? "").trim(),
        set_by: userId,
        set_by_name: nameOf(userId),
        set_at: at,
        superseded_at: null,
        since: since(),
      };
      listed.push(baseline);
      send(201, baseline);
    });
    return true;
  }
  return false;
}
