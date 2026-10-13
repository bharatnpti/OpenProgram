// Mock handlers for the timeline lane: the timeline per project and pod (GET
// /projects/{id}/timeline, GET /pods/{id}/timeline) and why a row's date moved
// (GET /projects/{id}/delivery/impact). NOT real data: shapes follow
// src/api/generated.ts, names follow the seeded demo tenant. Wired from
// ../mock-api.mjs before the lane mocks it reads.
//
// The committed dates come from the reports lane's delivery read and the
// planning baseline from the baselines lane's, so a date set or a baseline marked
// in the mock shows on the timeline at once. Two datasets, as viz-overall has:
// - a month (the default): Checkout Revamp planned on 15 Sep, with markers of
//   every kind (scope added with and without the CR label, one ringed for Jira
//   hygiene, removed, reopened, closing late, a bug, a blocker opened and
//   cleared, the date moved), and a forecast band on every row but the
//   Storefront Pod's;
// - three days (`VIZ_HISTORY=short npm run mock`, or a `viz-history=short`
//   cookie): three kept days, so no row has a band yet and only the latest
//   events are marked.
// Identity Platform and Customer Insights have no planning baseline and no
// committed date: their rows start at the first kept day, faded, and run open.
//
// Who sees what follows the backend (core/application/timeline_service.py): a
// project's or release's row for its readers; a pod's whole for them and for who
// runs or belongs to it; a scrum master's other pods dates only; a developer no
// project timeline, only their own pod's row. Blockers for the pod blocker read,
// "added by" never for an executive or a developer.
import { pods, projects, roster } from "../mock-console.mjs";
import * as baselinesLane from "./baselines.mjs";
import { neededDays } from "./forecast-settings.mjs";
import * as reportsLane from "./reports.mjs";

const TODAY = "2026-10-06";
const PROJECT = "project-checkout";
const RELEASE = "rel-1-0";
const ADMIN = ["admin"];
/** Who set each committed date and why, by "<kind>:<id>:<day set>:<date>", from the reports lane. */
const SET_BY = new Map();

const addDays = (iso, days) => {
  const day = new Date(`${iso}T12:00:00Z`);
  day.setUTCDate(day.getUTCDate() + days);
  return day.toISOString().slice(0, 10);
};
const isWorkingDay = (iso) => ![0, 6].includes(new Date(`${iso}T12:00:00Z`).getUTCDay());
const addWorkingDays = (iso, count) => {
  let day = iso;
  let left = Math.abs(Math.round(count));
  while (left > 0) {
    day = addDays(day, count > 0 ? 1 : -1);
    if (isWorkingDay(day)) left -= 1;
  }
  return day;
};
const workingDaysBetween = (a, b) => {
  if (a === b) return 0;
  const [from, to, sign] = a < b ? [a, b, 1] : [b, a, -1];
  let days = 0;
  for (let day = addDays(from, 1); day <= to; day = addDays(day, 1)) if (isWorkingDay(day)) days++;
  return sign * days;
};
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const short = (iso) => {
  const day = new Date(`${iso}T12:00:00Z`);
  return `${day.getUTCDate()} ${MONTHS[day.getUTCMonth()]}`;
};

function shortWanted(req) {
  if (process.env.VIZ_HISTORY === "short") return true;
  return /(?:^|;\s*)viz-history=short(?:;|$)/.test(req.headers.cookie ?? "");
}

/** Read another lane's mock as an admin, for the facts the timeline shares with it. */
function readLane(lane, req, path) {
  let found = null;
  lane.api(
    { ...req, method: "GET" },
    new URL(`http://mock${path}`),
    ADMIN,
    "U1001",
    (status, body) => {
      if (status === 200) found = body;
    },
    () => true,
  );
  return found;
}

const item = (key, title, stage, extra = {}) => ({
  key,
  title,
  stage,
  group: null,
  change_label: null,
  added_by: null,
  waits_on: null,
  hygiene: null,
  ...extra,
});
const marker = (day, kind, items, extra = {}) => ({
  day,
  kind,
  count: items.length || 1,
  items,
  change_label: null,
  ...extra,
});

// ---- Checkout Revamp's events --------------------------------------------------------

const CHK = {
  saved: item("CHK-109", "Saved cards", "groomed", {
    change_label: true,
    added_by: "Mina Patel",
  }),
  art: item("CHK-111", "Card art", "raised", {
    change_label: false,
    added_by: "Liam Chen",
    hygiene: true,
  }),
  wallet: item("CHK-112", "Wallet pass", "raised", { change_label: false, added_by: "Liam Chen" }),
  refund: item("CHK-102", "Refund flow", "in_testing"),
  bug: item("CHK-113", "Card form rejects a valid expiry", null, { group: "bug" }),
  secure: item("CHK-103", "3-D Secure challenge", "in_development", {
    waits_on: "Waiting on the sandbox keys from the payments vendor",
  }),
  gift: item("CHK-106", "Gift cards", "raised"),
  tokens: item("CHK-099", "Card tokenisation", "in_testing"),
};

/** Every event on Checkout's rows, by row; the three-day dataset keeps the latest only. */
const EVENTS = {
  project: [
    marker("2026-09-22", "scope_added", [CHK.saved], { change_label: true }),
    marker("2026-09-24", "late", [CHK.refund]),
    marker("2026-09-25", "blocker_opened", [CHK.secure]),
    marker("2026-09-28", "scope_removed", [CHK.gift]),
    marker("2026-09-30", "scope_added", [CHK.art, CHK.wallet], { change_label: false }),
    marker("2026-10-01", "bug", [CHK.bug]),
    marker("2026-10-02", "blocker_closed", [CHK.secure]),
    marker("2026-10-05", "reopened", [CHK.tokens]),
  ],
  "pod-payments": [
    marker("2026-09-22", "scope_added", [CHK.saved], { change_label: true }),
    marker("2026-09-24", "late", [CHK.refund]),
    marker("2026-09-25", "blocker_opened", [CHK.secure]),
    marker("2026-09-30", "scope_added", [CHK.art, CHK.wallet], { change_label: false }),
    marker("2026-10-01", "bug", [CHK.bug]),
    marker("2026-10-02", "blocker_closed", [CHK.secure]),
    marker("2026-10-05", "reopened", [CHK.tokens]),
  ],
  "pod-storefront": [marker("2026-09-28", "scope_removed", [CHK.gift])],
  [RELEASE]: [
    marker("2026-09-24", "late", [CHK.refund]),
    marker("2026-10-05", "reopened", [CHK.tokens]),
  ],
};
/** Events the three-day dataset still marks: the ones not read from kept forecast days. */
const SHORT_KINDS = new Set(["late", "bug", "blocker_opened", "blocker_closed", "reopened"]);

/** Each row's own facts: name, progress, scope, band and team date, per dataset. */
const CHECKOUT = {
  project: {
    name: "Checkout Revamp",
    progress: [8, 24],
    scope: [21, 24],
    band: ["2026-11-04", "2026-11-12"],
    team: ["2026-11-03", "CHK-103", 3],
    verdict: "off_track",
    shortVerdict: "off_track",
  },
  [RELEASE]: {
    name: "Release 1.0",
    progress: [2, 7],
    scope: [7, 7],
    band: ["2026-10-29", "2026-11-04"],
    team: ["2026-10-28", "CHK-101", 0],
    verdict: "at_risk",
    shortVerdict: "on_track",
  },
  "pod-payments": {
    name: "Payments Pod",
    progress: [4, 15],
    scope: [12, 15],
    band: ["2026-11-05", "2026-11-13"],
    team: ["2026-11-03", "CHK-103", 2],
    verdict: "off_track",
    shortVerdict: "on_track",
  },
  "pod-storefront": {
    name: "Storefront Pod",
    progress: [3, 9],
    scope: [10, 9],
    band: null,
    team: ["2026-10-15", "CHK-109", 1],
    verdict: "at_risk",
    shortVerdict: "at_risk",
  },
};

/** A project with no planning baseline and no committed date yet. */
function unplanned(projectId, name, podId, podName, keyPrefix) {
  const events = [
    marker("2026-09-29", "bug", [
      item(`${keyPrefix}-9`, "Session cookie not cleared on logout", null, { group: "bug" }),
    ]),
    marker("2026-09-30", "blocker_opened", [
      item(`${keyPrefix}-3`, "Assertion mapping", "in_development", {
        waits_on: "Waiting on the identity provider's test tenant",
      }),
    ]),
    marker("2026-10-01", "late", [item(`${keyPrefix}-4`, "Metadata import", "in_development")]),
  ];
  const facts = {
    progress: [3, 11],
    scope: null,
    band: ["2026-11-16", "2026-11-27"],
    team: [null, null, 4],
    verdict: "no_date",
    shortVerdict: "no_date",
  };
  return {
    project: { ...facts, name, events },
    [podId]: { ...facts, name: podName, events },
  };
}
const UNPLANNED = {
  "project-identity": unplanned(
    "project-identity",
    "Identity Platform",
    "pod-identity",
    "Identity Pod",
    "IDP",
  ),
  "project-insights": unplanned(
    "project-insights",
    "Customer Insights",
    "pod-data",
    "Data Pod",
    "CIN",
  ),
};

// ---- who sees what ------------------------------------------------------------------

const has = (roles, ...wanted) =>
  roles.includes("admin") || wanted.some((role) => roles.includes(role));
const readsProject = (roles) => has(roles, "po", "mgr", "exec");
const ownPods = (userId) => roster.find((person) => person.id === userId)?.pods ?? [];

function podAccess(roles, userId, podId) {
  const own = ownPods(userId).includes(podId) && has(roles, "sm", "dev");
  if (readsProject(roles) || own) return { level: "panel", own };
  if (has(roles, "sm", "mgr")) return { level: "dates", own: false };
  return null;
}

/** A row for the reader: dates only drops progress, scope and markers. */
function shaped(row, level, flags) {
  const whole = level === "panel";
  const markers = whole
    ? row.markers
        .filter((m) => flags.blockers || !m.kind.startsWith("blocker"))
        .map((m) => ({
          ...m,
          items: m.items.map((i) => ({ ...i, added_by: flags.names ? i.added_by : null })),
        }))
    : [];
  return {
    ...row,
    access: level,
    progress: whole ? row.progress : null,
    scope: whole ? row.scope : null,
    markers,
    can_set_date: flags.canSet,
  };
}

// ---- the rows ---------------------------------------------------------------------

function committedOf(req, projectId) {
  if (projectId !== PROJECT) return {};
  const delivery = readLane(reportsLane, req, `/projects/${PROJECT}/delivery`);
  if (!delivery) return {};
  const ticks = (scope) =>
    scope.commitment.changes
      .filter((change) => change.target_date)
      .map((change) => {
        const tick = { date: change.target_date, on: change.changed_at.slice(0, 10) };
        // Who and why stay out of the row (the timeline's ticks carry neither); the
        // impact read's committed lane says them.
        SET_BY.set(`${scope.scope_kind}:${scope.scope_id}:${tick.on}:${tick.date}`, {
          by: change.changed_by_name,
          note: change.note,
        });
        return tick;
      });
  const found = {
    project: { date: delivery.project.commitment.target_date, history: ticks(delivery.project) },
  };
  for (const scope of [...delivery.pods, ...delivery.releases]) {
    found[scope.scope_id] = { date: scope.commitment.target_date, history: ticks(scope) };
  }
  return found;
}

function baselineOf(req, projectId, releaseId) {
  const read = readLane(
    baselinesLane,
    req,
    `/projects/${projectId}/baselines${releaseId ? `?release_id=${releaseId}` : ""}`,
  );
  const own = read?.active ?? null;
  if (own || !releaseId) return own;
  // A release with no baseline of its own reads its project's, as the server does.
  return readLane(baselinesLane, req, `/projects/${projectId}/baselines`)?.active ?? null;
}

function sentence(row) {
  const parts = [];
  if (row.progress) parts.push(`${row.progress.done} of ${row.progress.total} done`);
  const committed = row.committed.date;
  if (!row.forecast) {
    parts.push(
      `${(row.forecast_reason ?? "no forecast yet").replace(/^N/, "n")}, ${committed ? `committed ${short(committed)}` : "no committed date"}`,
    );
  } else if (!committed) {
    parts.push(`the 85% date is ${short(row.forecast.p85)}, with no committed date`);
  } else {
    const gap = workingDaysBetween(committed, row.forecast.p85);
    parts.push(
      gap === 0
        ? `the 85% date is the committed date, ${short(committed)}`
        : `the 85% date, ${short(row.forecast.p85)}, is ${Math.abs(gap)} working ${Math.abs(gap) === 1 ? "day" : "days"} ${gap > 0 ? "after" : "before"} the committed ${short(committed)}`,
    );
  }
  const dated = row.committed.history.map((tick) => tick.date);
  const moves = dated.filter((day, i) => i > 0 && day !== dated[i - 1]).length;
  if (moves > 0) {
    parts.push(
      `the committed date moved ${moves === 1 ? "once" : moves === 2 ? "twice" : `${moves} times`}, ${short(dated[0])} → ${short(dated[dated.length - 1])}`,
    );
  }
  if (row.access === "panel") {
    if (!row.baseline) parts.push("planning is not marked complete yet");
    else {
      const added = row.markers.filter((m) => m.kind === "scope_added");
      const count = added.reduce((sum, m) => sum + m.count, 0);
      const without = added
        .filter((m) => m.change_label === false)
        .reduce((sum, m) => sum + m.count, 0);
      parts.push(
        count === 0
          ? "nothing added since planning"
          : `${count} ${count === 1 ? "requirement" : "requirements"} added since planning${without ? `, ${without} without the CR label` : ""}`,
      );
    }
  }
  return `${row.name}: ${parts.join("; ")}.`;
}

function buildRow({
  req,
  kind,
  id,
  projectId,
  projectName,
  facts,
  events,
  committed,
  baseline,
  asOf,
  reference,
}) {
  const shortSet = shortWanted(req);
  const firstKept = shortSet ? "2026-10-02" : "2026-09-08";
  const start = baseline ? baseline.planned_on : firstKept;
  const kept = (events ?? []).filter(
    (m) =>
      m.day <= asOf &&
      m.day > start &&
      (!shortSet || SHORT_KINDS.has(m.kind) || m.day >= firstKept),
  );
  const history = (committed?.history ?? []).filter((tick) => tick.on <= asOf);
  const committedDate = history.length ? history[history.length - 1].date : null;
  const band =
    !shortSet && facts.band ? { p50: facts.band[0], p85: facts.band[1], on: asOf } : null;
  const markers = [
    ...kept,
    ...history
      .filter((tick, i) => i > 0 && tick.on > start)
      .map((tick) => marker(tick.on, "committed_date_changed", [], { count: 1 })),
  ].sort((a, b) => a.day.localeCompare(b.day) || a.kind.localeCompare(b.kind));
  return {
    row: `${kind}:${id}`,
    scope_kind: kind,
    scope_id: id,
    project_id: projectId,
    project_name: projectName,
    name: facts.name,
    access: "panel",
    verdict: shortSet ? facts.shortVerdict : facts.verdict,
    baseline: baseline ? { day: baseline.planned_on, reconstructed: baseline.reconstructed } : null,
    start,
    committed: { date: committedDate, history },
    jira_release_date: kind === "release" ? "2026-10-30" : null,
    forecast: band,
    forecast_reason: band
      ? null
      : `No forecast yet: ${shortSet ? 2 : 1} of ${neededDays()} working days of history`,
    team: { date: facts.team[0], key: facts.team[1], undated: facts.team[2] },
    progress: { done: facts.progress[0], total: facts.progress[1], unit: "requirements" },
    scope: baseline && facts.scope ? { at_baseline: facts.scope[0], now: facts.scope[1] } : null,
    markers,
    reference_date: reference ?? null,
    can_set_date: false,
  };
}

function projectRows(req, projectId, releaseId, asOf) {
  const project = projects.find((p) => p.id === projectId);
  if (!project) return null;
  const committed = committedOf(req, projectId);
  const baseline = baselineOf(req, projectId, null);
  const facts = projectId === PROJECT ? CHECKOUT : UNPLANNED[projectId];
  const head = [];
  const scopes = releaseId ? [["release", releaseId]] : [["project", projectId]];
  if (!releaseId && projectId === PROJECT) scopes.push(["release", RELEASE]);
  for (const [kind, id] of scopes) {
    const key = kind === "project" ? "project" : id;
    const own = facts[key];
    if (!own) continue;
    head.push(
      buildRow({
        req,
        kind,
        id,
        projectId,
        projectName: project.name,
        facts: own,
        events: projectId === PROJECT ? EVENTS[key] : own.events,
        committed: committed[key],
        baseline: kind === "release" ? baselineOf(req, projectId, id) : baseline,
        asOf,
      }),
    );
  }
  const projectTarget = committed.project?.date ?? null;
  const podRows = project.pod_ids.map((podId) =>
    buildRow({
      req,
      kind: "pod",
      id: podId,
      projectId,
      projectName: project.name,
      facts: facts[podId] ?? {
        ...facts.project,
        name: pods.find((p) => p.id === podId)?.name ?? podId,
      },
      events: projectId === PROJECT ? (EVENTS[podId] ?? []) : (facts[podId]?.events ?? []),
      committed: committed[podId],
      baseline,
      asOf,
      reference: projectTarget,
    }),
  );
  const order = { off_track: 0, at_risk: 1, no_date: 2, not_enough_data: 3, on_track: 5, done: 6 };
  podRows.sort(
    (a, b) => (order[a.verdict] ?? 4) - (order[b.verdict] ?? 4) || a.name.localeCompare(b.name),
  );
  return { head, pods: podRows };
}

function timelineOf(rows, asOf) {
  const dates = rows.flatMap((row) => [
    row.committed.date,
    ...row.committed.history.map((tick) => tick.date),
    row.forecast?.p85,
    row.team.date,
    row.jira_release_date,
    row.reference_date,
  ]);
  const start = rows.map((row) => row.start).sort()[0] ?? addDays(asOf, -14);
  const last = [asOf, ...dates.filter(Boolean)].sort().at(-1);
  let end = addDays(last, 7);
  if ((Date.parse(end) - Date.parse(start)) / 86_400_000 < 28) end = addDays(start, 28);
  return {
    axis: { start, end, today: asOf },
    rows,
    text: rows.map((row) => ({
      row: row.row,
      scope_id: row.scope_id,
      project_id: row.project_id,
      sentence: sentence(row),
    })),
    change_label: "CR",
  };
}

function flagsFor(roles, kind, podAccessOf) {
  const names = has(roles, "po", "mgr", "sm");
  const blockers = has(roles, "sm", "mgr");
  if (kind !== "pod") return { names, blockers, canSet: has(roles, "po", "mgr") };
  return {
    names: names && !(podAccessOf?.own && roles.includes("dev")),
    blockers: blockers || Boolean(podAccessOf?.own && roles.includes("dev")),
    canSet: has(roles, "mgr") || Boolean(podAccessOf?.own && roles.includes("sm")),
  };
}

// ---- why a row's date moved ----------------------------------------------------------

const PERIOD_DAYS = { last_7_days: 7, last_30_days: 30 };

function impactOf(req, roles, projectId, scope, period, measure, asOf) {
  const rows = projectRows(req, projectId, scope.kind === "release" ? scope.id : null, asOf);
  const all = [...rows.head, ...rows.pods];
  const row = all.find((r) => r.scope_kind === scope.kind && r.scope_id === scope.id);
  if (!row) return null;
  const baseline = row.baseline?.day ?? null;
  const lastChange = row.committed.history.at(-1)?.on ?? null;
  const from =
    period === "since_baseline" && baseline
      ? baseline
      : period === "since_date_change" && lastChange
        ? lastChange
        : addDays(asOf, -(PERIOD_DAYS[period] ?? 30));
  const words =
    period === "since_baseline" && baseline
      ? `since planning (${short(baseline)})`
      : period === "since_date_change" && lastChange
        ? `since the date last changed (${short(lastChange)})`
        : `in the last ${PERIOD_DAYS[period] ?? 30} days`;
  const notes = [];
  if (period === "since_baseline" && !baseline) {
    notes.push("Planning is not marked complete yet, so this covers the last 30 days.");
  }
  const within = (m) => m.day > from && m.day <= asOf;
  const added = row.markers.filter((m) => m.kind === "scope_added" && within(m));
  const factor = measure === "p50" ? 0.6 : 1;
  const itemOf = (i, days) => ({
    key: i.key,
    title: i.title,
    type: "Story",
    working_days: days,
    day: null,
    group: i.change_label ? "change_request" : i.change_label === false ? "no_label" : null,
    change_label: i.change_label,
    added_by: has(roles, "po", "mgr", "sm") ? i.added_by : null,
  });
  const drivers = [];
  if (!row.forecast) {
    notes.push(
      `No forecast yet: Only 2 working days of history; a forecast needs ${neededDays()}.`,
    );
  } else {
    for (const [group, labelled] of [
      ["change_request", true],
      ["no_label", false],
    ]) {
      const items = added.filter((m) => m.change_label === labelled).flatMap((m) => m.items);
      if (items.length === 0) continue;
      const days = Math.round(2.5 * items.length * factor * 10) / 10;
      drivers.push({
        kind: "scope_added",
        group,
        working_days: days,
        p50: Math.round(2.5 * items.length * 0.6 * 10) / 10,
        p85: 2.5 * items.length,
        items: items.map((i) => itemOf(i, Math.round((days / items.length) * 10) / 10)),
        more: 0,
        note: `${items.length} ${items.length === 1 ? "requirement" : "requirements"} added ${labelled ? "as change requests" : "without the CR label"}`,
      });
    }
    const late = row.markers.filter((m) => m.kind === "late" && within(m)).flatMap((m) => m.items);
    drivers.push({
      kind: "pace",
      group: null,
      working_days: 2 * factor,
      p50: 1.2,
      p85: 2,
      items: [],
      more: 0,
      note: `3 finished${late.length ? `; ${late.map((i) => i.key).join(", ")} late` : ""}`,
    });
    const removed = row.markers
      .filter((m) => m.kind === "scope_removed" && within(m))
      .flatMap((m) => m.items);
    if (removed.length) {
      drivers.push({
        kind: "scope_removed",
        group: null,
        working_days: -1 * factor,
        p50: -0.6,
        p85: -1,
        items: removed.map((i) => itemOf(i, -1 * factor)),
        more: 0,
        note: `${removed.length} requirement removed`,
      });
    }
    drivers.push({
      kind: "throughput",
      group: null,
      working_days: -1 * factor,
      p50: -0.6,
      p85: -1,
      items: [],
      more: 0,
      note: "The 30-day window lost a slow day (11 Sep); yesterday finished 1. The average went from 0.5 to 0.6 a day.",
    });
    drivers.sort((a, b) => b.working_days - a.working_days);
  }
  const moved = row.forecast
    ? Math.round(drivers.reduce((sum, d) => sum + d.working_days, 0) * 10) / 10
    : null;
  const p85Now = row.forecast?.p85 ?? null;
  const p50Now = row.forecast?.p50 ?? null;
  const p85Then = p85Now ? addWorkingDays(p85Now, -drivers.reduce((s, d) => s + d.p85, 0)) : null;
  const p50Then = p50Now ? addWorkingDays(p50Now, -drivers.reduce((s, d) => s + d.p50, 0)) : null;
  const measureWords = measure === "p50" ? "50% date" : "85% date";
  const summary =
    moved === null
      ? `No forecast to compare ${words}.`
      : `The ${measureWords} moved ${Math.abs(moved)} working days ${moved >= 0 ? "later" : "earlier"} ${words}: ${drivers
          .filter((d) => d.working_days > 0)
          .map((d) => `+${d.working_days} from ${d.kind === "pace" ? "slower finishing" : d.note}`)
          .join(", ")}.`;
  const committedChanges = row.committed.history
    .map((tick, i, all) => ({ tick, before: all[i - 1] }))
    .filter(({ tick }) => tick.on > from && tick.on <= asOf)
    .map(({ tick, before }) => ({
      from_date: before?.date ?? null,
      to_date: tick.date,
      on: tick.on,
      by: SET_BY.get(`${scope.kind}:${scope.id}:${tick.on}:${tick.date}`)?.by ?? "a team member",
      note: SET_BY.get(`${scope.kind}:${scope.id}:${tick.on}:${tick.date}`)?.note ?? "",
      after_added: added.filter((m) => m.day <= tick.on).flatMap((m) => m.items.map((i) => i.key)),
    }));
  const lateItems = row.markers
    .filter((m) => m.kind === "late")
    .flatMap((m) =>
      m.items.map((i) => ({
        key: i.key,
        title: i.title,
        stage: i.stage ?? "in_development",
        date: addDays(m.day, -1),
        date_kind: "due",
        working_days_late: workingDaysBetween(addDays(m.day, -1), asOf),
      })),
    );
  return {
    scope_kind: scope.kind,
    scope_id: scope.id,
    project_id: projectId,
    name: row.name,
    project_name: row.project_name,
    period,
    period_words: words,
    measure,
    detail: row.access === "panel" ? "full" : "dates",
    change_label: "CR",
    baseline_day: baseline,
    start: {
      day: from,
      p50: p50Then,
      p85: p85Then,
      committed: row.committed.history[0]?.date ?? null,
      verdict: row.verdict,
    },
    end: {
      day: asOf,
      p50: p50Now,
      p85: p85Now,
      committed: row.committed.date,
      verdict: row.verdict,
    },
    moved_working_days: moved,
    unit: row.forecast ? "requirements" : null,
    summary,
    drivers,
    scope_changes: {
      added: added.flatMap((m) => m.items).map((i) => itemOf(i, 0)),
      removed: [],
      reopened: [],
      added_count: added.reduce((s, m) => s + m.count, 0),
      removed_count: 0,
      reopened_count: row.markers.filter((m) => m.kind === "reopened" && within(m)).length,
      finished_count: 3,
    },
    evidence: {
      late: lateItems,
      more_late: 0,
      undated: ["CHK-110", "CHK-111", "CHK-112"].slice(0, row.team.undated),
    },
    committed_changes: committedChanges,
    team_date: {
      from_date: "2026-10-21",
      from_key: "CHK-101",
      to_date: row.team.date,
      to_key: row.team.key,
      moves: row.team.date
        ? [
            {
              day: "2026-09-30",
              from_date: "2026-10-21",
              from_key: "CHK-101",
              to_date: row.team.date,
              to_key: row.team.key,
              cause: "eta_moved",
              words: `${row.team.key}'s ETA moved 21 Oct → ${short(row.team.date)} (stated 30 Sep).`,
            },
          ]
        : [],
    },
    // The timeline's own markers, with the same visibility: blockers for their readers only.
    markers: row.markers
      .filter((m) => within(m) && (has(roles, "sm", "mgr") || !m.kind.startsWith("blocker")))
      .map((m) => ({
        day: m.day,
        kind: m.kind,
        count: m.count,
        items: m.items.map((i) => ({
          key: i.key,
          title: i.title,
          stage: i.stage,
          group: i.group,
          added_by: has(roles, "po", "mgr", "sm") ? i.added_by : null,
        })),
        change_label: m.change_label,
      })),
    link: `/reports/${projectId}/overall?row=${scope.kind}:${scope.id}#timeline`,
    reconstructed_days: 0,
    not_known: notes,
  };
}

// ---- the routes -----------------------------------------------------------------------

/** Handles the call and returns true, or returns false for the next handler. */
// eslint-disable-next-line no-unused-vars -- `deny` is part of the lane handler signature.
export function api(req, url, roles, userId, send, deny) {
  const method = req.method ?? "GET";
  if (method !== "GET") return false;
  const reply = (status, body) => {
    send(status, body);
    return true;
  };
  const asOf = url.searchParams.get("as_of") || TODAY;
  let m;

  if ((m = url.pathname.match(/^\/projects\/([^/]+)\/timeline$/))) {
    if (!readsProject(roles) && !has(roles, "sm", "mgr"))
      return reply(403, { detail: "Not allowed." });
    const releaseId = url.searchParams.get("release_id");
    if (releaseId && releaseId !== RELEASE) {
      return reply(404, { detail: `No release '${releaseId}' in project '${m[1]}'.` });
    }
    const rows = projectRows(req, m[1], releaseId, asOf);
    if (!rows) return reply(404, { detail: `No project '${m[1]}'.` });
    const listed = [];
    if (readsProject(roles)) {
      for (const row of rows.head)
        listed.push(shaped(row, "panel", flagsFor(roles, row.scope_kind)));
    }
    for (const row of rows.pods) {
      const access = podAccess(roles, userId, row.scope_id);
      if (!access) continue;
      listed.push(
        shaped(
          row,
          access.level,
          access.level === "panel"
            ? flagsFor(roles, "pod", access)
            : { names: false, blockers: false, canSet: false },
        ),
      );
    }
    return reply(200, timelineOf(listed, asOf));
  }

  if ((m = url.pathname.match(/^\/pods\/([^/]+)\/timeline$/))) {
    const podId = m[1];
    const pod = pods.find((p) => p.id === podId);
    if (!pod) return reply(404, { detail: `No pod '${podId}'.` });
    const access = podAccess(roles, userId, podId);
    if (!access) {
      return reply(403, { detail: "You read your own pod's dates, and this is not your pod." });
    }
    const listed = pod.project_ids
      .map((projectId) =>
        projectRows(req, projectId, null, asOf)?.pods.find((row) => row.scope_id === podId),
      )
      .filter(Boolean)
      .map((row) =>
        shaped(
          row,
          access.level,
          access.level === "panel"
            ? flagsFor(roles, "pod", access)
            : { names: false, blockers: false, canSet: false },
        ),
      );
    return reply(200, timelineOf(listed, asOf));
  }

  if ((m = url.pathname.match(/^\/projects\/([^/]+)\/delivery\/impact$/))) {
    if (roles.length === 1 && roles[0] === "dev") return reply(403, { detail: "Not allowed." });
    const releaseId = url.searchParams.get("release_id");
    const podId = url.searchParams.get("pod_id");
    const scope = releaseId
      ? { kind: "release", id: releaseId }
      : podId
        ? { kind: "pod", id: podId }
        : { kind: "project", id: m[1] };
    if (scope.kind !== "pod" && !readsProject(roles)) {
      const theirs = ownPods(userId).some((id) =>
        pods.find((p) => p.id === id)?.project_ids.includes(m[1]),
      );
      if (!(has(roles, "sm") && theirs)) {
        return reply(403, {
          detail: "You read the projects your own pods work on, and this is not one of them.",
        });
      }
    }
    const body = impactOf(
      req,
      roles,
      m[1],
      scope,
      url.searchParams.get("period") || "since_baseline",
      url.searchParams.get("measure") || "p85",
      asOf,
    );
    if (!body)
      return reply(404, { detail: `No ${scope.kind} '${scope.id}' in project '${m[1]}'.` });
    if (scope.kind === "pod") {
      const access = podAccess(roles, userId, podId);
      if (!access) return reply(403, { detail: "Not allowed." });
      if (access.level !== "panel") {
        return reply(200, {
          ...body,
          detail: "dates",
          drivers: body.drivers.map((d) => ({ ...d, items: [], note: d.kind })),
          evidence: null,
          markers: [],
        });
      }
    }
    return reply(200, body);
  }
  return false;
}
