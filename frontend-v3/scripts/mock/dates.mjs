// Mock delivery dates for the date colours. NOT real data: shapes follow
// src/api/generated.ts, names follow the seeded demo tenant. Called by
// ../mock-api.mjs before the reports lane's handlers.
//
// GET /projects/{id}/delivery for the two projects the reports lane leaves
// empty, so every colour of a date strip shows somewhere: Identity Platform on
// track (the 85% date before the committed one, green) and Customer Insights
// with open work and no committed date (red). Checkout Revamp stays the reports
// lane's (off track, with an at-risk release and pod).

import { neededDays } from "./forecast-settings.mjs";

const PROGRESS = ["po", "mgr", "exec", "admin"];

/** Answers and says so: the router stops at the first handler that returns true. */
const reply = (send, status, body) => {
  send(status, body);
  return true;
};

const history = (p50, p85, remaining, extra = {}) => ({
  p50,
  p85,
  remaining,
  unit: "requirements",
  sample_days: 21,
  completed_in_sample: 6,
  reason: null,
  ...extra,
});

const noCommitment = {
  target_date: null,
  original_date: null,
  times_moved: 0,
  moved_days: null,
  changes: [],
};

const identity = {
  scope_kind: "project",
  scope_id: "project-identity",
  project_id: "project-identity",
  name: "Identity Platform",
  commitment: {
    target_date: "2026-11-20",
    original_date: "2026-11-20",
    times_moved: 0,
    moved_days: null,
    changes: [
      {
        target_date: "2026-11-20",
        changed_at: "2026-09-14T09:00:00Z",
        changed_by: "U1003",
        changed_by_name: "Mina Patel",
        note: "Agreed with the security review.",
      },
    ],
  },
  target: "2026-11-20",
  target_source: "committed",
  jira_release_date: null,
  history: history("2026-11-06", "2026-11-16", 7),
  team: { latest: "2026-11-13", latest_key: "IDP-214", dated: 7, undated: 0 },
  verdict: "on_track",
  reasons: [
    "Committed by Mina Patel.",
    "The completion rate says Fri 6 Nov (50%) to Mon 16 Nov (85%): in time.",
  ],
  total: 12,
  open: 7,
};

const insights = {
  scope_kind: "project",
  scope_id: "project-insights",
  project_id: "project-insights",
  name: "Customer Insights",
  commitment: noCommitment,
  target: null,
  target_source: null,
  jira_release_date: null,
  history: history("2026-11-27", "2026-12-08", 5),
  team: { latest: "2026-11-24", latest_key: "INS-031", dated: 3, undated: 2 },
  verdict: "no_date",
  reasons: ["No delivery date is committed yet."],
  total: 8,
  open: 5,
};

const DELIVERIES = { "project-identity": identity, "project-insights": insights };

export function api(req, url, roles, userId, send) {
  const p = url.pathname.replace(/^\/api\/v1/, "");
  const method = req.method ?? "GET";
  const has = (allowed) => roles.some((role) => allowed.includes(role));

  const m = p.match(/^\/projects\/([^/]+)\/delivery$/);
  if (m && method === "GET" && DELIVERIES[m[1]]) {
    if (!has(PROGRESS)) {
      return reply(send, 403, { detail: `${userId} is not authorized for read_project_progress` });
    }
    // The minimum in force when asked: Admin's Forecast setting (forecast-settings.mjs).
    const project = DELIVERIES[m[1]];
    const history = { ...project.history, needed_days: neededDays() };
    return reply(send, 200, { project: { ...project, history }, pods: [], releases: [] });
  }
  return false;
}
