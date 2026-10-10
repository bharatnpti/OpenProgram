// Mock endpoints for the scoped-delivery lane. NOT real data: shapes follow
// src/api/generated.ts (DeliveryTreeResponse), names follow the demo roster.
// Called by ../mock-api.mjs before the other lane mocks, because it answers
// reads they refuse, under the backend's rule (core/application/delivery_scope.py):
//
// - GET /me/delivery-tree: the person's own part of the tree, from the roster's
//   pods (each person's `pods`), with what each node opens on;
// - a scrum master reads the progress and dates of a project one of their pods
//   works on; another project is refused in the backend's words;
// - a developer reads their own pod's rollup, check-ins, blockers, tasks and
//   dates; another pod is refused in the backend's words.
//
// A scoped read that is allowed is answered by the other mocks, asked as a
// role that reads it everywhere, so its data is theirs. Everything else falls
// through, as before.
import * as consoleData from "../mock-console.mjs";
import * as adminStructure from "./admin-structure.mjs";
import * as datesMock from "./dates.mjs";
import * as reportsLane from "./reports.mjs";
import * as shellMock from "./shell.mjs";
import * as todayMock from "./today.mjs";
import * as vizOverall from "./viz-overall.mjs";

const TODAY = "2026-10-06";

const PROJECT_OUTSIDE_SCOPE =
  "You read the projects your own pods work on, and this is not one of them.";
const POD_OUTSIDE_SCOPE = "You read your own pod's details, and this is not your pod.";
const POD_DATES_OUTSIDE_SCOPE = "You read your own pod's dates, and this is not your pod.";

const has = (roles, ...wanted) =>
  roles.includes("admin") || roles.some((role) => wanted.includes(role));

/** The person's pods (the roster's), and the projects those pods work on. */
function scopeOf(userId) {
  const person = consoleData.roster.find((p) => p.id === userId);
  const pods = new Set(person?.pods ?? []);
  const projects = new Set(
    consoleData.projects
      .filter((project) => project.pod_ids.some((id) => pods.has(id)))
      .map((project) => project.id),
  );
  return { pods, projects };
}

const readsProject = (roles, own) =>
  has(roles, "po", "mgr", "exec") || (own && roles.includes("sm"));
const readsPodDetail = (roles, own) => has(roles, "sm", "mgr") || (own && roles.includes("dev"));
const readsPodDates = (roles, own) =>
  has(roles, "sm", "mgr", "po", "exec") || (own && roles.includes("dev"));

function podAccess(roles, own) {
  if (readsPodDetail(roles, own) && (own || has(roles, "mgr", "exec"))) return "panel";
  return readsPodDates(roles, own) ? "dates" : "name";
}

function tree(roles, userId) {
  const scope = scopeOf(userId);
  const listed = consoleData.projects.filter((project) => scope.projects.has(project.id));
  const node = (item, access, own, parents) => ({
    id: item.id,
    kind: item.kind,
    name: item.name,
    rag: access === "name" ? null : (item.rag ?? "unknown"),
    access,
    own,
    parent_ids: parents,
  });
  const programs = consoleData.programs
    .filter((program) => listed.some((project) => project.program_ids.includes(program.id)))
    .map((program) => node(program, has(roles, "mgr", "exec") ? "panel" : "name", true, []));
  const projects = listed.map((project) =>
    node(project, readsProject(roles, true) ? "panel" : "name", true, project.program_ids),
  );
  const pods = consoleData.pods
    .filter((pod) => pod.project_ids.some((id) => scope.projects.has(id)))
    .map((pod) => {
      const own = scope.pods.has(pod.id);
      return node(
        pod,
        podAccess(roles, own),
        own,
        pod.project_ids.filter((id) => scope.projects.has(id)),
      );
    });
  const byName = (a, b) => a.name.localeCompare(b.name);
  return {
    as_of: TODAY,
    programs: programs.sort(byName),
    projects: projects.sort(byName),
    pods: pods.sort(byName),
  };
}

/** The other mocks, in mock-api.mjs's order, asked as `roles`. */
function answerAs(req, url, roles, userId, send, deny) {
  for (const lane of [vizOverall, shellMock, datesMock, todayMock, adminStructure, reportsLane]) {
    if (lane.api(req, url, roles, userId, send, deny)) return true;
  }
  if (consoleData.consoleApi(req, url, roles, userId, send, deny) !== false) return true;
  send(404, { detail: "Not found." });
  return true;
}

const refuse = (send, detail) => {
  send(403, { detail });
  return true;
};

export function api(req, url, roles, userId, send, deny) {
  if ((req.method ?? "GET") !== "GET") return false;
  const p = url.pathname.replace(/^\/api\/v1/, "");
  let m;
  if (p === "/me/delivery-tree") {
    send(200, tree(roles, userId));
    return true;
  }
  if ((m = p.match(/^\/projects\/([^/]+)\/(progress|delivery)$/))) {
    if (readsProject(roles, false) || !roles.includes("sm")) return false;
    if (!scopeOf(userId).projects.has(decodeURIComponent(m[1]))) {
      return refuse(send, PROJECT_OUTSIDE_SCOPE);
    }
    return answerAs(req, url, ["po"], userId, send, deny);
  }
  if ((m = p.match(/^\/pods\/([^/]+)\/(rollup|checkins|blockers|tasks)$/))) {
    if (readsPodDetail(roles, false) || !roles.includes("dev")) return false;
    if (!scopeOf(userId).pods.has(decodeURIComponent(m[1]))) {
      return refuse(send, POD_OUTSIDE_SCOPE);
    }
    return answerAs(req, url, ["sm"], userId, send, deny);
  }
  if ((m = p.match(/^\/pods\/([^/]+)\/delivery$/))) {
    if (readsPodDates(roles, false) || !roles.includes("dev")) return false;
    if (!scopeOf(userId).pods.has(decodeURIComponent(m[1]))) {
      return refuse(send, POD_DATES_OUTSIDE_SCOPE);
    }
    // Read as a product owner, so the answer offers no date to set.
    return answerAs(req, url, ["po"], userId, send, deny);
  }
  return false;
}
