// Mock endpoints for the role-shaped redesign lane. NOT real data: shapes follow
// src/api/generated.ts. Called by ../mock-api.mjs before the generic console
// router.
//
// Signals › Risks reads every project's risks (GET /projects/{id}/risks) to group
// them by project. The generic mock answers only Checkout Revamp's (in
// mock-api.mjs); the other projects have none open, so they answer an empty list
// here, under the same rule as the backend: a team or executive aggregate reader
// (403 for a developer), and 404 for a project that does not exist.
import * as consoleData from "../mock-console.mjs";

const TODAY = "2026-10-06";
const AGGREGATE = ["sm", "po", "mgr", "exec", "admin"];

/** Answers and says so: the router stops at the first handler that returns true. */
const reply = (send, status, body) => {
  send(status, body);
  return true;
};

export function api(req, url, roles, _userId, send) {
  if ((req.method ?? "GET") !== "GET") return false;
  const match = url.pathname.replace(/^\/api\/v1/, "").match(/^\/projects\/([^/]+)\/risks$/);
  if (!match || match[1] === "project-checkout") return false;
  if (!roles.some((role) => AGGREGATE.includes(role))) {
    return reply(send, 403, { detail: "Not authorized for read_team_aggregate." });
  }
  if (!consoleData.projects.some((project) => project.id === match[1])) {
    return reply(send, 404, { detail: `No project '${match[1]}'.` });
  }
  return reply(send, 200, { project_id: match[1], as_of: TODAY, risks: [], drift: [] });
}
