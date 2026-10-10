// Mock handlers for the Jira writes lane: Admin › Jira writes (the tenant's switch, a
// switch per kind of write, the projects release readiness may create in, and the
// changes) and the older GET/PUT /config/tenant/writeback, which is the same master
// switch. NOT real data: shapes follow src/api/generated.ts and the rules follow
// core/domain/jira_writes.py, so every write round-trips. The release readiness mock
// reads its create switch and refusals from here. State resets when the mock restarts.

const KINDS = ["checkin_updates", "console_moves", "readiness_create"];
const DEFAULTS = { checkin_updates: true, console_moves: true, readiness_create: false };
const NAMES = { U1001: "Asha Rao" };
const PROJECT_KEY = /^[A-Z][A-Z0-9_]+$/;
const KIND_OFF = {
  checkin_updates: "Updating tickets from check-ins is off for this tenant.",
  console_moves: "Moving tickets from task updates is off for this tenant.",
  readiness_create: "Creating release-readiness issues is off for this tenant.",
};

// As the demo tenant has it: write-back switched on by an admin for check-ins, and
// nothing else set, so creating issues is off by default.
const state = {
  master: { on: true, source: "admin" },
  kinds: {},
  projects: null,
  changes: [
    {
      at: "2026-10-01T09:00:00Z",
      by: "U1001",
      setting: "master",
      before: false,
      after: true,
      before_source: "default",
    },
  ],
};

const kind = (name) => ({
  on: state.kinds[name] ?? DEFAULTS[name],
  source: name in state.kinds ? "admin" : "default",
});
const projects = () => state.projects ?? { own_project: true, projects: [] };

/** The create switch of release readiness, as its settings show it. */
export function readinessCreateOn() {
  return kind("readiness_create").on;
}

/** The tenant's master switch. */
export function masterOn() {
  return state.master.on;
}

/** Why a release readiness draft for this project may not be created now, or null. */
export function createRefusal(projectKey = "CHK", ownKeys = ["CHK"]) {
  if (!state.master.on) return "Jira writes are off for this tenant.";
  if (!readinessCreateOn()) return KIND_OFF.readiness_create;
  const allowed = projects();
  const key = String(projectKey ?? "").toUpperCase();
  if ((allowed.own_project && ownKeys.includes(key)) || allowed.projects.includes(key)) return null;
  const may = [...new Set([...(allowed.own_project ? ownKeys : []), ...allowed.projects])];
  return `This draft is for Jira project ${key}, where OpenProgram may not create issues. It may create them in ${may.join(", ")}.`;
}

/** Set one kind from another screen (release readiness's own settings), audited here. */
export function setKind(name, on, actor) {
  update({ [name]: on }, actor);
}

function value(setting) {
  if (setting === "master") return { value: state.master.on, source: state.master.source };
  if (setting === "create_projects") {
    return { value: projects(), source: state.projects ? "admin" : "default" };
  }
  return { value: kind(setting).on, source: kind(setting).source };
}

function update(body, actor) {
  const before = Object.fromEntries(
    ["master", ...KINDS, "create_projects"].map((setting) => [setting, value(setting)]),
  );
  if (typeof body.master === "boolean") state.master = { on: body.master, source: "admin" };
  for (const name of KINDS) {
    if (typeof body[name] === "boolean") state.kinds[name] = body[name];
  }
  if (body.create_projects) state.projects = body.create_projects;
  const at = new Date().toISOString();
  for (const setting of ["master", ...KINDS, "create_projects"]) {
    const after = value(setting);
    const was = before[setting];
    if (JSON.stringify(after) === JSON.stringify(was)) continue;
    state.changes.unshift({
      at,
      by: actor,
      setting,
      before: was.value,
      after: after.value,
      before_source: was.source,
    });
  }
}

function response() {
  return {
    master: state.master,
    kinds: KINDS.map((name) => ({
      kind: name,
      ...kind(name),
      effective: state.master.on && kind(name).on,
    })),
    create_projects: { ...projects(), source: state.projects ? "admin" : "default" },
    changes: state.changes.slice(0, 20).map((change) => {
      const isProjects = change.setting === "create_projects";
      return {
        at: change.at,
        by: change.by,
        by_name: NAMES[change.by] ?? null,
        setting: change.setting,
        before_on: isProjects ? null : change.before,
        after_on: isProjects ? null : change.after,
        before_projects: isProjects ? change.before : null,
        after_projects: isProjects ? change.after : null,
        before_source: change.before_source,
      };
    }),
  };
}

/** The 422 a request the server would refuse gets, in its words; null when it is fine. */
function problem(body) {
  const known = new Set(["master", ...KINDS, "create_projects"]);
  const unknown = Object.keys(body).filter((key) => !known.has(key));
  if (unknown.length > 0) {
    return {
      detail: unknown.map((key) => ({
        type: "extra_forbidden",
        loc: ["body", key],
        msg: "Extra inputs are not permitted",
      })),
    };
  }
  const cp = body.create_projects;
  if (!cp) return null;
  const keys = [
    ...new Set((cp.projects ?? []).map((key) => String(key).trim().toUpperCase()).filter(Boolean)),
  ];
  const bad = keys.filter((key) => !PROJECT_KEY.test(key));
  if (bad.length > 0) {
    return {
      detail: `A Jira project key is capital letters and digits, such as CHK: ${bad.join(", ")}.`,
    };
  }
  if (!cp.own_project && keys.length === 0) {
    return {
      detail:
        "Allow at least one project: the scope's own, or a project key. To stop creating issues, turn off Create release-readiness issues.",
    };
  }
  body.create_projects = { own_project: Boolean(cp.own_project), projects: keys };
  return null;
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

/** Answers this lane's endpoints and returns true, or returns false for the next handler. */
export function api(req, url, roles, actingAs, send, deny) {
  const p = url.pathname;
  const method = req.method ?? "GET";
  if (p !== "/config/tenant/jira-writes" && p !== "/config/tenant/writeback") return false;
  // manage_config: an admin only, reading or writing.
  if (!roles.includes("admin")) {
    deny();
    return true;
  }
  const actor = actingAs ?? "U1001";
  if (method === "GET") {
    if (p === "/config/tenant/writeback") {
      send(200, {
        enabled: state.master.on,
        source: state.master.source === "admin" ? "tenant" : "default",
      });
    } else {
      send(200, response());
    }
    return true;
  }
  if (method !== "PUT") return false;
  readJson(req).then((body) => {
    if (body === undefined) return send(422, { detail: "The request body is not JSON." });
    if (p === "/config/tenant/writeback") {
      update({ master: Boolean(body.enabled) }, actor);
      return send(200, { enabled: state.master.on, source: "tenant" });
    }
    const refused = problem(body);
    if (refused) return send(422, refused);
    update(body, actor);
    return send(200, response());
  });
  return true;
}
