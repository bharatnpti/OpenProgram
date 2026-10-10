// Mock handlers for the viz-overall lane: Overall's visuals. NOT real data: shapes
// follow src/api/generated.ts, names follow the seeded demo tenant. Wired from
// ../mock-api.mjs before the other lanes' handlers.
//
// Two datasets for Checkout Revamp, the same project either way:
// - a month (the default): the reports lane's 30 days of snapshots, its date set
//   on 1 Sep and moved on 29 Sep, and here the forecast as it stood each day
//   (GET /projects/{id}/delivery/history), narrowing towards today's;
// - three days (`VIZ_HISTORY=short npm run mock`, or a `viz-history=short`
//   cookie): the same reads cut to the last three days of snapshots, so nothing
//   forecasts yet, the verdict falls back on the team's dates, and the date was
//   set once.
// Writes stay the reports lane's, so they round-trip in either dataset.
import { neededDays } from "./forecast-settings.mjs";
import * as reportsLane from "./reports.mjs";

const TODAY = "2026-10-06";
const PROJECT = "project-checkout";
const PROGRESS = ["po", "mgr", "exec", "admin"];
const SHORT_DAYS = 3;
/** The first day of the short dataset's history: three working days, Fri 2 to Tue 6 Oct. */
const WINDOW_START = "2026-10-02";

const reply = (send, status, body) => {
  send(status, body);
  return true;
};

const addDays = (iso, days) => {
  const day = new Date(`${iso}T12:00:00Z`);
  day.setUTCDate(day.getUTCDate() + days);
  return day.toISOString().slice(0, 10);
};
const daysBetween = (a, b) =>
  Math.round((Date.parse(`${b}T12:00:00Z`) - Date.parse(`${a}T12:00:00Z`)) / 86_400_000);
const isWorkingDay = (iso) => ![0, 6].includes(new Date(`${iso}T12:00:00Z`).getUTCDay());

function shortWanted(req) {
  if (process.env.VIZ_HISTORY === "short") return true;
  return /(?:^|;\s*)viz-history=short(?:;|$)/.test(req.headers.cookie ?? "");
}

/**
 * The forecast as it stood each day, for the month: nothing until ten working
 * days of history, then a range that starts wide and late-leaning and narrows
 * to today's (the reports lane's 4 Nov to 12 Nov for the project).
 */
function monthHistory(scope, today) {
  const last = { p50: today.p50, p85: today.p85 };
  const days = [];
  let working = 0;
  for (let offset = 29; offset >= 0; offset--) {
    const day = addDays(TODAY, -offset);
    if (isWorkingDay(day)) working += 1;
    if (working < 11 || !last.p50 || !last.p85) {
      days.push({ day, p50: null, p85: null, sample_days: Math.max(0, working - 1) });
      continue;
    }
    // From 14 days before today the range narrows linearly to today's.
    const t = Math.min(1, (29 - offset - 14) / 15);
    const p50 = addDays(last.p50, Math.round(-6 * (1 - t)));
    const p85 = addDays(last.p85, Math.round(12 * (1 - t)));
    days.push({ day, p50, p85, sample_days: Math.min(21, working - 1) });
  }
  return {
    project_id: PROJECT,
    release_id: scope === "project" ? null : scope,
    scope_kind: scope === "project" ? "project" : "release",
    days,
    needed_days: neededDays(),
  };
}

/** Like core/domain/forecast.py `verdict`, without history: the team's dates decide. */
function teamVerdict(scope) {
  if (scope.total === 0 || scope.open === 0) return "done";
  if (!scope.target) return "no_date";
  if (!scope.team.latest) return "not_enough_data";
  if (scope.team.latest > scope.target) return "off_track";
  return scope.team.undated > 0 ? "at_risk" : "on_track";
}

/** Changes before the window collapse into the last one, set on the window's first day. */
function shortCommitment(commitment) {
  const before = commitment.changes.filter((c) => c.changed_at.slice(0, 10) < WINDOW_START);
  const after = commitment.changes.filter((c) => c.changed_at.slice(0, 10) >= WINDOW_START);
  const kept = before.length
    ? [{ ...before[before.length - 1], changed_at: `${WINDOW_START}T09:00:00Z` }, ...after]
    : after;
  const dated = kept.filter((c) => c.target_date);
  let moved = 0;
  for (let i = 1; i < dated.length; i++) {
    if (dated[i].target_date !== dated[i - 1].target_date) moved += 1;
  }
  const target = kept.length ? kept[kept.length - 1].target_date : null;
  const original = dated[0]?.target_date ?? null;
  return {
    target_date: target,
    original_date: original,
    times_moved: moved,
    moved_days: target && original && moved ? daysBetween(original, target) : null,
    changes: kept,
  };
}

/** A scope as three days of history leave it: no forecast, the team's dates decide. */
function shortScope(scope) {
  const commitment = shortCommitment(scope.commitment);
  const target = commitment.target_date ?? scope.jira_release_date ?? null;
  const reason = `Only 2 working days of history; a forecast needs ${neededDays()}.`;
  const next = {
    ...scope,
    commitment,
    target,
    target_source: commitment.target_date
      ? "committed"
      : scope.jira_release_date
        ? "jira_release"
        : null,
    history: {
      ...scope.history,
      p50: null,
      p85: null,
      sample_days: 2,
      completed_in_sample: 0,
      reason,
    },
  };
  next.verdict = teamVerdict(next);
  next.reasons = [
    ...scope.reasons.filter((line) => !/history|completion rate/i.test(line)),
    reason,
  ];
  return next;
}

const shortDelivery = (body) => ({
  ...body,
  project: shortScope(body.project),
  pods: body.pods.map(shortScope),
  releases: body.releases.map(shortScope),
});

const shortRequirements = (body) => ({
  ...body,
  // The last three working days' snapshots: two have the working day before, as the forecast counts.
  timeline: body.timeline.filter((point) => isWorkingDay(point.day)).slice(-SHORT_DAYS),
  previous_day: body.timeline.length > 1 ? body.timeline[body.timeline.length - 2].day : null,
});

const shortPod = (body) => ({
  ...body,
  projects: body.projects.map((item) => ({ ...item, pod: shortScope(item.pod) })),
});

/** Handles the call and returns true, or returns false for the next handler. */
export function api(req, url, roles, userId, send, deny) {
  const p = url.pathname;
  const method = req.method ?? "GET";
  const reads = roles.includes("admin") || roles.some((role) => PROGRESS.includes(role));
  let m;

  if ((m = p.match(/^\/projects\/([^/]+)\/delivery\/history$/)) && method === "GET") {
    if (!reads)
      return reply(send, 403, { detail: `${userId} is not authorized for read_project_progress` });
    const releaseId = url.searchParams.get("release_id");
    if (m[1] !== PROJECT) {
      return reply(send, 200, {
        project_id: m[1],
        release_id: releaseId,
        scope_kind: releaseId ? "release" : "project",
        days: [{ day: TODAY, p50: null, p85: null, sample_days: 0 }],
        needed_days: neededDays(),
      });
    }
    // Today's forecast is the delivery read's, so the chart ends where the strip says.
    let today = null;
    reportsLane.api(
      { ...req, method: "GET" },
      new URL(`http://mock/projects/${PROJECT}/delivery`),
      roles,
      userId,
      (status, body) => {
        if (status !== 200) return;
        const scope = releaseId
          ? body.releases.find((r) => r.scope_id === releaseId)
          : body.project;
        today = scope?.history ?? null;
      },
      deny,
    );
    if (releaseId && !today) return reply(send, 404, { detail: "No such release." });
    if (shortWanted(req)) {
      return reply(send, 200, {
        project_id: PROJECT,
        release_id: releaseId,
        scope_kind: releaseId ? "release" : "project",
        days: [4, 1, 0].map((back, i) => ({
          day: addDays(TODAY, -back),
          p50: null,
          p85: null,
          sample_days: i,
        })),
        needed_days: neededDays(),
      });
    }
    return reply(send, 200, monthHistory(releaseId ?? "project", today ?? {}));
  }

  if (!shortWanted(req) || method !== "GET" || url.searchParams.get("as_of")) return false;
  const wrap = (transform) =>
    reportsLane.api(
      req,
      url,
      roles,
      userId,
      (status, body) => send(status, status === 200 ? transform(body) : body),
      deny,
    );
  if (p === `/projects/${PROJECT}/delivery`) return wrap(shortDelivery);
  if (p === `/projects/${PROJECT}/requirements`) return wrap(shortRequirements);
  if (/^\/pods\/[^/]+\/delivery$/.test(p)) return wrap(shortPod);
  return false;
}
