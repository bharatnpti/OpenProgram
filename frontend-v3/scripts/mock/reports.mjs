// Mock handlers for the reports lane: Overall's forecast, releases, requirements,
// gates and questions, and the Delivery pod panel's dates. NOT real data: shapes
// follow src/api/generated.ts, names follow the seeded demo tenant. Changes are
// kept in memory while the mock runs, so every write round-trips.
//
// Who may do what follows core/application/authorization.py: dates of a project
// or release and releases themselves for the product owner, manager and admin;
// a pod's date for its own scrum master, a manager or an admin; gate items and
// questions for everyone but the executive, sign-off only for the roles a kind
// names. Wired from scripts/mock-api.mjs, called before the console handlers.
import { pods as directoryPods, roster } from "../mock-console.mjs";

const TODAY = "2026-10-06";
const PROJECT = "project-checkout";
const nameOf = (id) => roster.find((p) => p.id === id)?.name ?? id;

// ---- Requirements -------------------------------------------------------------

const STAGES = [
  "raised",
  "groomed",
  "in_development",
  "in_testing",
  "business_testing",
  "production",
];
const LABELS = {
  raised: "Raised",
  groomed: "Groomed",
  in_development: "In development",
  in_testing: "In testing",
  business_testing: "Business testing",
  production: "Production",
};
const start = [7, 5, 6, 2, 1, 1],
  end = [2, 4, 6, 3, 2, 7];
// Average story points of a requirement in each stage, from today's points per stage.
const POINTS_EACH = [3, 5.25, 5.67, 6, 6.5, 14.14];
const timeline = [];
for (let i = 0; i < 30; i++) {
  const d = new Date(Date.UTC(2026, 8, 7 + i));
  const t = i / 29;
  const counts = {};
  STAGES.forEach((s, k) => {
    counts[s] = Math.round(start[k] + (end[k] - start[k]) * t + (k === 2 ? Math.sin(i / 3) : 0));
  });
  if (i === 29)
    STAGES.forEach((s, k) => {
      counts[s] = end[k];
    });
  // Every requirement carries points here, so the burn-down draws by story points.
  const points = Object.fromEntries(
    STAGES.map((s, k) => [s, Math.round(counts[s] * POINTS_EACH[k] * 10) / 10]),
  );
  timeline.push({ day: d.toISOString().slice(0, 10), counts, points, has_points: true });
}
const reqRows = [
  ["CHK-104", "Capture retry runbook", "production", "Done", "Liam Chen", 5, "2026-10-05"],
  ["CHK-099", "Saved cards list", "production", "Done", "Zoe Almeida", 8, "2026-09-24"],
  ["CHK-100", "Payment method picker", "production", "Done", "Tom Okafor", 13, "2026-09-18"],
  ["CHK-102", "Refund edge cases", "business_testing", "UAT", "Noah Weber", 8, "2026-09-29"],
  [
    "CHK-107",
    "Order confirmation e-mail",
    "business_testing",
    "UAT",
    "Sofia Bergmann",
    3,
    "2026-10-02",
  ],
  ["CHK-101", "Payment intent API", "in_testing", "In QA", "Liam Chen", 13, "2026-10-05"],
  ["CHK-108", "Address autocomplete", "in_testing", "In QA", "Zoe Almeida", 5, "2026-10-01"],
  ["CHK-103", "3-D Secure step-up", "in_development", "Blocked", "Kai Thompson", 13, "2026-09-22"],
  [
    "CHK-105",
    "Cart price breakdown",
    "in_development",
    "In Progress",
    "Tom Okafor",
    5,
    "2026-09-30",
  ],
  ["CHK-109", "Cart promo stacking", "groomed", "Ready for dev", "Tom Okafor", 8, "2026-10-05"],
  ["CHK-111", "Wallet pay button", "groomed", "Ready for dev", null, 5, "2026-10-01"],
  ["CHK-112", "Gift card split tender", "raised", "Backlog", null, null, "2026-10-02"],
];
const rowFor = (key) => reqRows.find((row) => row[0] === key);

function requirements(release) {
  const rows = release ? reqRows.filter(([key]) => release.keys.includes(key)) : reqRows;
  const share = release ? rows.length / 24 : 1;
  const counts = Object.fromEntries(STAGES.map((s) => [s, 0]));
  rows.forEach(([, , stage]) => (counts[stage] += 1));
  const points = rows.reduce((sum, row) => sum + (row[5] ?? 0), 0);
  const donePoints = rows
    .filter((row) => row[2] === "production")
    .reduce((sum, row) => sum + (row[5] ?? 0), 0);
  const total = release ? rows.length : 24;
  const done = release ? counts.production : 7;
  return {
    project_id: PROJECT,
    project_name: "Checkout Revamp",
    as_of: TODAY,
    release_id: release?.release_id ?? null,
    release_name: release?.name ?? null,
    live: true,
    available: true,
    total,
    done,
    percent_complete: release ? (donePoints / Math.max(1, points)) * 100 : 58.2,
    has_points: !release,
    points_total: release ? points : 170,
    points_done: release ? donePoints : 99,
    stages: STAGES.map((s, k) => ({
      stage: s,
      label: LABELS[s],
      count: release ? counts[s] : end[k],
      points: release ? 0 : [6, 21, 34, 18, 13, 99][k],
      change: release ? null : [-1, 1, -1, 1, -1, 1][k],
    })),
    timeline: release
      ? timeline.map((point) => ({
          day: point.day,
          counts: Object.fromEntries(STAGES.map((s) => [s, Math.round(point.counts[s] * share)])),
          // A release's requirements here carry no points: its burn-down is by count.
          points: {},
          has_points: false,
        }))
      : timeline,
    moves: release
      ? []
      : [
          {
            key: "CHK-104",
            title: "Capture retry runbook",
            from_stage: "business_testing",
            to_stage: "production",
          },
          {
            key: "CHK-109",
            title: "Cart promo stacking",
            from_stage: "raised",
            to_stage: "groomed",
          },
          {
            key: "CHK-101",
            title: "Payment intent API",
            from_stage: "in_development",
            to_stage: "in_testing",
          },
        ],
    previous_day: release ? null : "2026-10-05",
    unmapped_statuses: ["Backlog"],
    excluded: 1,
    requirements: rows.map(([key, title, stage, status, who, pts, since]) => ({
      key,
      title,
      stage,
      status,
      mapped: status !== "Backlog",
      assignee_name: who,
      story_points: release ? null : pts,
      due_date: null,
      in_stage_since: since,
    })),
  };
}

function emptyRequirements(projectId, name) {
  return {
    ...requirements(null),
    project_id: projectId,
    project_name: name,
    available: false,
    total: 0,
    done: 0,
    percent_complete: null,
    has_points: false,
    points_total: 0,
    points_done: 0,
    stages: STAGES.map((s) => ({ stage: s, label: LABELS[s], count: 0, points: 0, change: null })),
    timeline: [],
    moves: [],
    previous_day: null,
    unmapped_statuses: [],
    excluded: 0,
    requirements: [],
  };
}

// ---- Releases and committed dates ----------------------------------------------

const releases = [
  {
    release_id: "rel-1-0",
    project_id: PROJECT,
    name: "Release 1.0",
    match_kind: "fix_version",
    match_value: "Release 1.0",
    updated_at: "2026-09-01T09:00:00Z",
    updated_by: "U1003",
    keys: ["CHK-099", "CHK-100", "CHK-101", "CHK-102", "CHK-104", "CHK-107", "CHK-108"],
  },
];
const candidates = [
  { kind: "fix_version", value: "Release 1.0", release_date: "2026-10-30" },
  { kind: "fix_version", value: "Release 1.1", release_date: "2026-11-20" },
  { kind: "label", value: "payments", release_date: null },
];
const candidateKeys = {
  "fix_version:Release 1.0": releases[0].keys,
  "fix_version:Release 1.1": ["CHK-103", "CHK-105", "CHK-109", "CHK-111", "CHK-112"],
  "label:payments": ["CHK-099", "CHK-100", "CHK-101", "CHK-102", "CHK-103", "CHK-104"],
};

// Every committed date, by scope key ("project:…", "pod:…", "release:…").
const changes = {
  [`project:${PROJECT}`]: [
    {
      target_date: "2026-10-23",
      changed_at: "2026-09-01T09:12:00Z",
      changed_by: "U1003",
      changed_by_name: "Mina Patel",
      note: "First commitment, agreed in the release planning.",
    },
    {
      target_date: "2026-10-30",
      changed_at: "2026-09-29T14:40:00Z",
      changed_by: "U1003",
      changed_by_name: "Mina Patel",
      note: "3-D Secure scope added.",
    },
  ],
  "pod:pod-payments": [
    {
      target_date: "2026-11-03",
      changed_at: "2026-09-30T10:00:00Z",
      changed_by: "U1006",
      changed_by_name: "Ira Novak",
      note: "Sandbox credentials are the critical path.",
    },
  ],
  "pod:pod-storefront": [
    {
      target_date: "2026-10-16",
      changed_at: "2026-09-30T10:05:00Z",
      changed_by: "U1014",
      changed_by_name: "Ben Sorensen",
      note: "",
    },
  ],
  "release:rel-1-0": [
    {
      target_date: "2026-10-30",
      changed_at: "2026-09-01T09:15:00Z",
      changed_by: "U1003",
      changed_by_name: "Mina Patel",
      note: "",
    },
  ],
};

const daysBetween = (a, b) =>
  Math.round((Date.parse(`${b}T12:00:00Z`) - Date.parse(`${a}T12:00:00Z`)) / 86_400_000);
/** "Tue 3 Nov", the way the server words a day in a reason. */
const dayWords = (iso) =>
  new Date(`${iso}T12:00:00Z`).toLocaleDateString("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });

/** Like core/domain/forecast.py commitment_from_changes: the last date, how it moved. */
function commitmentOf(key) {
  const list = changes[key] ?? [];
  const dates = list.map((c) => c.target_date).filter(Boolean);
  const target = list.length ? list[list.length - 1].target_date : null;
  const original = dates[0] ?? null;
  let moved = 0;
  for (let i = 1; i < list.length; i++) {
    if (list[i].target_date !== list[i - 1].target_date && list[i - 1].target_date) moved += 1;
  }
  return {
    target_date: target,
    original_date: original,
    times_moved: moved,
    moved_days: target && original && moved ? daysBetween(original, target) : null,
    changes: list,
  };
}

const history = (p50, p85, remaining) => ({
  p50,
  p85,
  remaining,
  unit: "requirements",
  sample_days: 21,
  completed_in_sample: 6,
  reason: null,
});

function scope(kind, id, name, total, open, hist, team, verdict, reasons, jiraDate = null) {
  const commitment = commitmentOf(`${kind}:${id}`);
  const target = commitment.target_date ?? jiraDate;
  return {
    scope_kind: kind,
    scope_id: id,
    project_id: PROJECT,
    name,
    commitment,
    target,
    target_source: commitment.target_date ? "committed" : jiraDate ? "jira_release" : null,
    jira_release_date: jiraDate,
    history: hist,
    team,
    verdict: target ? verdict : total === 0 ? "done" : "no_date",
    reasons: target ? reasons : ["No delivery date is committed yet."],
    total,
    open,
  };
}

function podScope(podId) {
  if (podId === "pod-payments") {
    return scope(
      "pod",
      podId,
      "Payments Pod",
      15,
      11,
      history("2026-11-05", "2026-11-13", 11),
      { latest: "2026-11-03", latest_key: "CHK-103", dated: 9, undated: 2 },
      "off_track",
      ["Planned later than the project's 30 Oct."],
    );
  }
  // A short history cannot forecast, so the team's own dates decide (core/domain/forecast.py
  // `verdict`): the latest date is before the pod's 16 Oct, but one requirement has none.
  return scope(
    "pod",
    podId,
    "Storefront Pod",
    9,
    6,
    {
      ...history(null, null, 6),
      sample_days: 1,
      completed_in_sample: 0,
      reason: "Only 1 working day of history; a forecast needs 10.",
    },
    { latest: "2026-10-15", latest_key: "CHK-109", dated: 5, undated: 1 },
    "at_risk",
    [
      "Only 1 working day of history; a forecast needs 10.",
      "Team dates: the latest open requirement is due Thu 15 Oct 2026 (CHK-109).",
      "1 open requirement has no ETA or due date.",
    ],
  );
}

function releaseScope(release) {
  const rows = reqRows.filter(([key]) => release.keys.includes(key));
  const open = rows.filter((row) => row[2] !== "production").length;
  const jira = candidates.find(
    (c) => c.kind === release.match_kind && c.value === release.match_value,
  )?.release_date;
  return scope(
    "release",
    release.release_id,
    release.name,
    rows.length,
    open,
    history("2026-10-29", "2026-11-04", open),
    { latest: "2026-10-28", latest_key: "CHK-101", dated: open, undated: 0 },
    "at_risk",
    [`${open} of its ${rows.length} requirements are still open.`],
    jira ?? null,
  );
}

function delivery() {
  const pods = ["pod-payments", "pod-storefront"].map(podScope);
  const later = pods.filter(
    (p) =>
      p.target &&
      commitmentOf(`project:${PROJECT}`).target_date &&
      p.target > commitmentOf(`project:${PROJECT}`).target_date,
  );
  return {
    project: scope(
      "project",
      PROJECT,
      "Checkout Revamp",
      24,
      17,
      history("2026-11-04", "2026-11-12", 17),
      { latest: "2026-11-03", latest_key: "CHK-103", dated: 14, undated: 3 },
      "off_track",
      [
        "Committed by Mina Patel; moved once, by 7 days.",
        "The completion rate says Wed 4 Nov (50%) to Thu 12 Nov (85%): 3 to 9 working days late.",
        "The team's own dates say Tue 3 Nov (CHK-103).",
        ...later.map(
          (p) => `${p.name} plans its part for ${dayWords(p.target)}, later than the project.`,
        ),
      ],
      "2026-10-30",
    ),
    pods,
    releases: releases.map(releaseScope),
  };
}

function emptyScope(kind, id, name, projectId) {
  return {
    scope_kind: kind,
    scope_id: id,
    project_id: projectId,
    name,
    commitment: {
      target_date: null,
      original_date: null,
      times_moved: 0,
      moved_days: null,
      changes: [],
    },
    target: null,
    target_source: null,
    jira_release_date: null,
    history: { ...history(null, null, 0), sample_days: 0, completed_in_sample: 0 },
    team: { latest: null, latest_key: null, dated: 0, undated: 0 },
    verdict: "done",
    reasons: ["Every requirement is in production."],
    total: 0,
    open: 0,
  };
}

/** A scrum master runs a pod they belong to; a manager or admin runs every pod. */
const runsPod = (roles, userId, podId) =>
  roles.includes("admin") ||
  roles.includes("mgr") ||
  (roles.includes("sm") && (roster.find((p) => p.id === userId)?.pods ?? []).includes(podId));

function setDate(key, body, userId) {
  const target = body?.target_date ?? null;
  if (target && daysBetween(target, TODAY) > 365) {
    return [422, { detail: "A delivery date more than a year in the past is not a plan." }];
  }
  (changes[key] ??= []).push({
    target_date: target,
    changed_at: new Date().toISOString(),
    changed_by: userId,
    changed_by_name: nameOf(userId),
    note: String(body?.note ?? "").trim(),
  });
  return [200, commitmentOf(key)];
}

// ---- Gates and questions --------------------------------------------------------

const templates = [
  {
    template_id: "business-acceptance",
    name: "Business acceptance",
    guards_stage: "production",
    enabled: true,
    issue_types: [],
    kinds: [
      {
        key: "acceptance",
        label: "Acceptance criterion",
        sign_off_roles: ["po", "mgr"],
        evidence_required: false,
        headings: ["Acceptance criteria", "AC", "Definition of done"],
        gherkin: false,
      },
    ],
  },
  {
    template_id: "engineering-delivery",
    name: "Engineering delivery",
    guards_stage: "business_testing",
    enabled: true,
    issue_types: [],
    kinds: [
      {
        key: "test_case",
        label: "Test case",
        sign_off_roles: ["dev", "sm"],
        evidence_required: true,
        headings: ["Test cases", "Test plan"],
        gherkin: true,
      },
    ],
  },
];
const GATED = ["CHK-104", "CHK-102", "CHK-101", "CHK-103", "CHK-109"];

let nextItem = 1;
const item = (issueKey, templateId, kind, text, status, extra = {}) => ({
  item_id: `item-${nextItem++}`,
  issue_key: issueKey,
  template_id: templateId,
  kind,
  text,
  status,
  source: status === "suggested" ? "description" : "manual",
  source_ref: status === "suggested" ? "description" : "",
  created_by: status === "suggested" ? "scan" : "U1003",
  signed_by: ["met", "failed", "waived"].includes(status)
    ? kind === "test_case"
      ? "U1009"
      : "U1003"
    : null,
  signed_at: ["met", "failed", "waived"].includes(status) ? "2026-10-02T10:00:00Z" : null,
  evidence_url:
    kind === "test_case" && status === "met" ? "https://ci.acme.example/runs/4182" : null,
  note: "",
  ...extra,
});
const BA = "business-acceptance";
const ED = "engineering-delivery";
const items = [
  item(
    "CHK-104",
    BA,
    "acceptance",
    "A failed capture is retried three times, then alerts on-call.",
    "met",
  ),
  item("CHK-104", BA, "acceptance", "The runbook names who restarts the capture worker.", "met"),
  item(
    "CHK-104",
    ED,
    "test_case",
    "Given a capture timeout, when it retries, then it succeeds once.",
    "met",
  ),
  item(
    "CHK-102",
    BA,
    "acceptance",
    "A partial refund keeps the original order's tax lines.",
    "met",
  ),
  item("CHK-102", BA, "acceptance", "A refund over the captured amount is refused.", "met"),
  item("CHK-102", BA, "acceptance", "Refunds after 90 days need a manager's approval.", "pending"),
  item("CHK-102", ED, "test_case", "TC-402 partial refund rounding.", "failed", {
    note: "Rounds half down; expected half up.",
  }),
  item("CHK-102", ED, "test_case", "TC-417 refund across three tenders.", "met"),
  item("CHK-101", BA, "acceptance", "Capturing an intent twice returns the first result.", "met"),
  item("CHK-101", BA, "acceptance", "An expired intent cannot be captured.", "pending"),
  item("CHK-101", ED, "test_case", "Given an expired intent, when captured, then 409.", "met"),
  item(
    "CHK-103",
    BA,
    "acceptance",
    "A challenged card completes 3-D Secure in the same tab.",
    "suggested",
  ),
  item(
    "CHK-103",
    BA,
    "acceptance",
    "A failed challenge offers another payment method.",
    "suggested",
  ),
  item("CHK-103", BA, "acceptance", "Frictionless cards skip the challenge.", "suggested"),
  item(
    "CHK-103",
    ED,
    "test_case",
    "Given the ACS simulator, when challenged, then success.",
    "pending",
  ),
  item("CHK-109", BA, "acceptance", "Two promos stack only when both allow it.", "pending"),
  item(
    "CHK-109",
    ED,
    "test_case",
    "Given two stackable promos, when applied, then both show.",
    "suggested",
  ),
];

/** Like core/domain/gates.py evaluate_gate. */
function evaluate(template, issueItems) {
  const mine = issueItems.filter((i) => i.template_id === template.template_id);
  const kept = mine.filter((i) => ["pending", "met", "failed", "waived"].includes(i.status));
  const missing = template.kinds
    .filter((k) => !kept.some((i) => i.kind === k.key))
    .map((k) => k.key);
  const met = kept.filter((i) => i.status === "met" || i.status === "waived").length;
  const state = kept.some((i) => i.status === "failed")
    ? "failed"
    : missing.length
      ? "missing"
      : met === kept.length
        ? "passed"
        : "open";
  return {
    template_id: template.template_id,
    state,
    met,
    total: kept.length,
    suggested: mine.filter((i) => i.status === "suggested").length,
    missing_kinds: missing,
  };
}

const questions = [
  [
    "q1",
    "CHK-112",
    "U1002",
    "U1003",
    "2026-10-06T09:40:00Z",
    "Maximum tenders per order for a gift-card split?",
    "not_yet",
    true,
  ],
  [
    "q2",
    "CHK-102",
    "U1004",
    "U1003",
    "2026-09-29T13:10:00Z",
    "Do partial refunds need a separate ledger entry?",
    "not_yet",
    true,
  ],
  [
    "q3",
    "CHK-103",
    "U1007",
    "U1008",
    "2026-10-01T10:05:00Z",
    "Which ACS simulator do we certify the challenge flow against?",
    "partly",
    true,
  ],
  [
    "q4",
    "CHK-101",
    "U1002",
    "U1003",
    "2026-10-02T08:30:00Z",
    "Is a 24-hour idempotency key TTL acceptable?",
    "answered",
    true,
  ],
  [
    "q5",
    "CHK-109",
    "U1012",
    "U1003",
    "2026-09-30T15:00:00Z",
    "Can the promo-stacking rules from the epic be linked here?",
    "not_yet",
    false,
  ],
].map(([id, key, by, to, at, summary, status, confirmed]) => ({
  question_id: id,
  issue_key: key,
  comment_ref: `c-${id}`,
  asked_by: by,
  asked_by_name: nameOf(by),
  asked_to: to,
  asked_to_name: nameOf(to),
  asked_at: at,
  summary,
  status,
  confirmed,
  status_set_by_person: false,
  answered_ref: status === "answered" ? `c-${id}-answer` : null,
  dismissed: false,
}));
const questionResponse = ({ dismissed, ...rest }) => rest;

function board(release) {
  // Questions belong to every requirement in scope; only some carry gate items here.
  const scopeKeys = release ? release.keys : reqRows.map((row) => row[0]);
  const keys = GATED.filter((key) => scopeKeys.includes(key));
  const issues = keys.map((key) => {
    const [, title, stage, status] = rowFor(key);
    const mine = items.filter((i) => i.issue_key === key && i.status !== "dismissed");
    const evaluations = templates.map((t) => evaluate(t, mine));
    const passedWithout = templates
      .filter(
        (t, k) =>
          STAGES.indexOf(stage) >= STAGES.indexOf(t.guards_stage) &&
          evaluations[k].state !== "passed",
      )
      .map((t) => t.name);
    return { key, title, stage, status, evaluations, items: mine, passed_without: passedWithout };
  });
  const actors = new Set(items.flatMap((i) => [i.signed_by, i.created_by]).filter(Boolean));
  return {
    project_id: PROJECT,
    release_id: release?.release_id ?? null,
    templates,
    issues,
    questions: questions
      .filter((q) => !q.dismissed && scopeKeys.includes(q.issue_key))
      .map(questionResponse),
    actor_names: Object.fromEntries(
      [...actors].filter((id) => id !== "scan").map((id) => [id, nameOf(id)]),
    ),
  };
}

let scanned = false;

// ---- Routing ------------------------------------------------------------------------

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

const projects = {
  "project-checkout": "Checkout Revamp",
  "project-identity": "Identity Platform",
  "project-insights": "Customer Insights",
};

/** Handles the call and returns true, or returns false for the next handler. */
// eslint-disable-next-line no-unused-vars -- `deny` is part of the lane handler signature.
export function api(req, url, roles, userId, send, deny) {
  const p = url.pathname;
  const method = req.method ?? "GET";
  const has = (...r) => roles.includes("admin") || roles.some((x) => r.includes(x));
  const progress = () => has("po", "mgr", "exec");
  const projectDates = () => has("po", "mgr");
  const editGates = () => has("dev", "sm", "po", "mgr");
  const reply = (status, body) => {
    send(status, body);
    return true;
  };
  // The backend's own wording for a missing capability.
  const notAuthorized = (capability) =>
    reply(403, { detail: `${userId} is not authorized for ${capability}` });
  // A write answers once its body is read; the call itself is handled now.
  const withBody = (handle) => {
    void readBody(req).then(handle);
    return true;
  };
  let m;

  if ((m = p.match(/^\/projects\/([^/]+)\/delivery$/)) && method === "GET") {
    if (!progress()) return notAuthorized("read_project_progress");
    if (m[1] === PROJECT) return reply(200, delivery());
    if (!projects[m[1]]) return false;
    return reply(200, {
      project: emptyScope("project", m[1], projects[m[1]], m[1]),
      pods: [],
      releases: [],
    });
  }
  if ((m = p.match(/^\/projects\/([^/]+)\/delivery-date$/)) && method === "PUT") {
    if (!projectDates()) return notAuthorized("set_project_dates");
    return withBody((body) => send(...setDate(`project:${m[1]}`, body, userId)));
  }
  if (
    (m = p.match(/^\/projects\/([^/]+)\/releases\/([^/]+)\/delivery-date$/)) &&
    method === "PUT"
  ) {
    if (!projectDates()) return notAuthorized("set_project_dates");
    if (!releases.some((r) => r.release_id === m[2]))
      return reply(404, { detail: "No such release." });
    return withBody((body) => send(...setDate(`release:${m[2]}`, body, userId)));
  }
  if ((m = p.match(/^\/projects\/([^/]+)\/pods\/([^/]+)\/delivery-date$/)) && method === "PUT") {
    if (!has("sm", "mgr")) return notAuthorized("set_pod_dates");
    if (!runsPod(roles, userId, m[2])) {
      return reply(403, {
        detail: "Only the pod's own scrum master or a manager sets the pod's date.",
      });
    }
    return withBody((body) => send(...setDate(`pod:${m[2]}`, body, userId)));
  }
  if ((m = p.match(/^\/pods\/([^/]+)\/delivery$/)) && method === "GET") {
    if (!(progress() || has("sm", "mgr"))) return reply(403, { detail: "Not allowed." });
    const pod = directoryPods.find((item) => item.id === m[1]);
    if (!pod) return reply(404, { detail: `No pod '${m[1]}'.` });
    const projectTarget = (projectId) =>
      projectId === PROJECT ? commitmentOf(`project:${PROJECT}`).target_date : null;
    return reply(200, {
      pod_id: pod.id,
      can_set_dates: has("sm", "mgr") && runsPod(roles, userId, pod.id),
      projects: pod.project_ids.map((projectId) => ({
        project_id: projectId,
        project_name: projects[projectId] ?? projectId,
        project_target: projectTarget(projectId),
        pod:
          projectId === PROJECT && ["pod-payments", "pod-storefront"].includes(pod.id)
            ? podScope(pod.id)
            : emptyScope("pod", pod.id, pod.name, projectId),
      })),
    });
  }
  if ((m = p.match(/^\/projects\/([^/]+)\/releases$/))) {
    if (method === "GET") {
      if (!progress()) return notAuthorized("read_project_progress");
      return reply(200, m[1] === PROJECT ? releases.map(({ keys, ...r }) => r) : []);
    }
    if (method === "POST") {
      if (!projectDates()) return notAuthorized("set_project_dates");
      return withBody((body) => {
        const name = String(body?.name ?? "").trim();
        const value = String(body?.match_value ?? "").trim();
        if (!name || !value) {
          return send(422, {
            detail: "A release needs a name and the fix version or label it is.",
          });
        }
        const release = {
          release_id: `rel-${Date.now().toString(36)}`,
          project_id: m[1],
          name,
          match_kind: body.match_kind,
          match_value: value,
          updated_at: new Date().toISOString(),
          updated_by: userId,
          keys: candidateKeys[`${body.match_kind}:${value}`] ?? [],
        };
        releases.push(release);
        const { keys, ...response } = release;
        send(201, response);
      });
    }
  }
  if ((m = p.match(/^\/projects\/([^/]+)\/releases\/([^/]+)$/)) && method === "DELETE") {
    if (!projectDates()) return notAuthorized("set_project_dates");
    const at = releases.findIndex((r) => r.release_id === m[2]);
    if (at >= 0) releases.splice(at, 1);
    return reply(204, null);
  }
  if ((m = p.match(/^\/projects\/([^/]+)\/release-candidates$/))) {
    if (!projectDates()) return notAuthorized("set_project_dates");
    const issues = (c) => (candidateKeys[`${c.kind}:${c.value}`] ?? []).length;
    return reply(200, m[1] === PROJECT ? candidates.map((c) => ({ ...c, issues: issues(c) })) : []);
  }
  if ((m = p.match(/^\/projects\/([^/]+)\/requirements$/))) {
    if (!progress()) return notAuthorized("read_project_progress");
    if (m[1] !== PROJECT) return reply(200, emptyRequirements(m[1], projects[m[1]] ?? m[1]));
    const releaseId = url.searchParams.get("release_id");
    const release = releaseId ? releases.find((r) => r.release_id === releaseId) : null;
    if (releaseId && !release) return reply(404, { detail: "No such release." });
    return reply(200, requirements(release));
  }
  if ((m = p.match(/^\/projects\/([^/]+)\/gates$/))) {
    if (!(progress() || editGates())) return reply(403, { detail: "Not allowed." });
    if (m[1] !== PROJECT) {
      return reply(200, {
        project_id: m[1],
        release_id: null,
        templates,
        issues: [],
        questions: [],
        actor_names: {},
      });
    }
    const releaseId = url.searchParams.get("release_id");
    const release = releaseId ? releases.find((r) => r.release_id === releaseId) : null;
    if (releaseId && !release) return reply(404, { detail: "No such release." });
    return reply(200, board(release));
  }
  if ((m = p.match(/^\/projects\/([^/]+)\/gates\/scan$/)) && method === "POST") {
    if (!editGates()) return notAuthorized("edit_gates");
    const read = m[1] === PROJECT ? GATED.length : 0;
    if (!scanned && read) {
      items.push(
        item(
          "CHK-101",
          ED,
          "test_case",
          "Given a duplicate capture, when retried, then one charge.",
          "suggested",
        ),
      );
    }
    // The first read finds one new suggestion; after that nothing changed in Jira.
    const first = !scanned && read > 0;
    scanned = true;
    return reply(200, {
      read: first ? read : 0,
      unchanged: first ? 0 : read,
      failed: 0,
      suggested_items: first ? 1 : 0,
      questions: 0,
    });
  }
  if ((m = p.match(/^\/issues\/([^/]+)\/gate-items$/)) && method === "POST") {
    if (!editGates()) return notAuthorized("edit_gates");
    return withBody((body) => {
      const template = templates.find((t) => t.template_id === body?.template_id);
      if (!template || !template.kinds.some((k) => k.key === body?.kind)) {
        return send(422, { detail: "That gate has no such kind of item." });
      }
      const text = String(body?.text ?? "").trim();
      if (!text) return send(422, { detail: "An item needs its text." });
      const added = item(m[1], template.template_id, body.kind, text, "pending", {
        created_by: userId,
      });
      items.push(added);
      send(201, added);
    });
  }
  if ((m = p.match(/^\/gate-items\/([^/]+)\/(confirm|dismiss)$/)) && method === "POST") {
    if (!editGates()) return notAuthorized("edit_gates");
    const found = items.find((i) => i.item_id === m[1]);
    if (!found) return reply(404, { detail: `No item '${m[1]}'.` });
    if (m[2] === "dismiss") found.status = "dismissed";
    else if (["suggested", "dismissed"].includes(found.status)) found.status = "pending";
    return reply(200, found);
  }
  if ((m = p.match(/^\/gate-items\/([^/]+)\/sign-off$/)) && method === "PUT") {
    if (!editGates()) return notAuthorized("edit_gates");
    const found = items.find((i) => i.item_id === m[1]);
    if (!found) return reply(404, { detail: `No item '${m[1]}'.` });
    const kind = templates
      .find((t) => t.template_id === found.template_id)
      .kinds.find((k) => k.key === found.kind);
    return withBody((body) => {
      if (body?.status === "pending") {
        Object.assign(found, { status: "pending", signed_by: null, signed_at: null });
        return send(200, found);
      }
      if (!roles.includes("admin") && !kind.sign_off_roles.some((r) => roles.includes(r))) {
        const who = kind.sign_off_roles
          .map(
            (r) =>
              ({ dev: "developer", sm: "scrum master", po: "product owner", mgr: "manager" })[r] ??
              r,
          )
          .join(" or ");
        return send(403, { detail: `Only a ${who} signs off a ${kind.label.toLowerCase()}.` });
      }
      const evidence = String(body?.evidence_url ?? "").trim() || found.evidence_url;
      if (body?.status === "met" && kind.evidence_required && !evidence) {
        return send(422, {
          detail: `A ${kind.label.toLowerCase()} is met only with a link to its evidence.`,
        });
      }
      if (evidence && !/^https?:\/\//.test(evidence)) {
        return send(422, { detail: "Evidence is a link starting with https://." });
      }
      Object.assign(found, {
        status: body.status,
        signed_by: userId,
        signed_at: new Date().toISOString(),
        evidence_url: evidence ?? null,
        note: String(body?.note ?? "").trim() || found.note,
      });
      send(200, found);
    });
  }
  if ((m = p.match(/^\/questions\/([^/]+)$/)) && method === "PUT") {
    if (!editGates()) return notAuthorized("edit_gates");
    const found = questions.find((q) => q.question_id === m[1]);
    if (!found) return reply(404, { detail: `No question '${m[1]}'.` });
    return withBody((body) => {
      if (typeof body?.confirmed === "boolean") found.confirmed = body.confirmed;
      if (typeof body?.dismissed === "boolean") found.dismissed = body.dismissed;
      if (body?.status) Object.assign(found, { status: body.status, status_set_by_person: true });
      send(200, questionResponse(found));
    });
  }
  if ((m = p.match(/^\/issues\/([^/]+)\/questions$/)) && method === "POST") {
    if (!editGates()) return notAuthorized("edit_gates");
    return withBody((body) => {
      const askedTo = String(body?.asked_to ?? "").trim();
      const summary = String(body?.summary ?? "").trim();
      if (!askedTo || !summary)
        return send(422, { detail: "A question needs who it is asked of and what." });
      const added = {
        question_id: `q-${Date.now().toString(36)}`,
        issue_key: m[1],
        comment_ref: "",
        asked_by: userId,
        asked_by_name: nameOf(userId),
        asked_to: askedTo,
        asked_to_name: askedTo,
        asked_at: new Date().toISOString(),
        summary,
        status: "not_yet",
        confirmed: true,
        status_set_by_person: false,
        answered_ref: null,
        dismissed: false,
      };
      questions.push(added);
      send(201, questionResponse(added));
    });
  }
  return false;
}
