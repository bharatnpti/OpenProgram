// Mock handlers for the portfolio's bulk reads (GET /portfolio/delivery,
// /portfolio/scope, /portfolio/warnings). NOT real data: shapes follow
// src/api/generated.ts, names follow the seeded demo tenant's mock roster. Who may
// read what follows the backend: the dates for the project readers (product owner,
// manager, executive, admin) and a scrum master's own; scope the same, an
// executive reading hygiene as counts; warnings for the aggregate readers, the
// blockers listed to the pods' blocker readers. An executive's bodies name nobody.
// Wired from ../mock-api.mjs before the lane mocks.

const TODAY = "2026-10-06";
const COMPUTED = "2026-10-06T08:50:00Z";
const has = (roles, ...wanted) => roles.some((role) => role === "admin" || wanted.includes(role));

const counts = (verdicts) => {
  const found = {
    on_track: 0,
    at_risk: 0,
    off_track: 0,
    done: 0,
    no_date: 0,
    not_enough_data: 0,
    none: 0,
  };
  for (const verdict of verdicts) found[verdict ?? "none"] += 1;
  return found;
};

const PROJECTS = [
  {
    id: "project-checkout",
    name: "Checkout Revamp",
    program_id: "program-digital",
    program_name: "Digital Platform Program",
    verdict: "off_track",
    target: "2026-11-20",
    target_source: "committed",
    p50: "2026-12-04",
    p85: "2026-12-18",
    team_date: "2026-12-01",
    team_key: "CHK-104",
    days_late: 20,
    reason:
      "Off track because 3 requirements were added after planning (15 Sep), 2 without the CR label, pushing it back 9 working days.",
    health: "2 open blockers, 1 of 6 not replied",
    health_day: TODAY,
    done: 9,
    total: 24,
    pods: [
      {
        id: "pod-payments",
        name: "Payments Pod",
        done: 5,
        total: 14,
        verdict: "off_track",
        target: "2026-11-20",
        p85: "2026-12-18",
      },
      {
        id: "pod-storefront",
        name: "Storefront Pod",
        done: 4,
        total: 10,
        verdict: "at_risk",
        target: "2026-11-20",
        p85: "2026-11-27",
      },
    ],
    releases: [
      {
        id: "rel-1-0",
        name: "Release 1.0",
        verdict: "at_risk",
        target: "2026-11-01",
        p85: "2026-11-09",
      },
    ],
    marks: [
      {
        day: "2026-09-22",
        kind: "scope_added",
        count: 2,
        working_days: 6,
        pushed: true,
        moved_to: null,
      },
      {
        day: "2026-09-29",
        kind: "committed_date_changed",
        count: 1,
        working_days: null,
        pushed: false,
        moved_to: "2026-11-20",
      },
      {
        day: "2026-10-02",
        kind: "scope_added",
        count: 1,
        working_days: 3,
        pushed: true,
        moved_to: null,
      },
      {
        day: "2026-10-05",
        kind: "scope_removed",
        count: 1,
        working_days: -1.5,
        pushed: false,
        moved_to: null,
      },
    ],
    plan: {
      planned_on: "2026-09-15",
      planned: 21,
      added: 3,
      without_label: 2,
      without_label_percent: 67,
      change_label: "CR",
      removed: 0,
      added_days: 9,
    },
    start: "2026-09-15",
    kept_on: TODAY,
    computed_at: COMPUTED,
    reconstructed: false,
  },
  {
    id: "project-identity",
    name: "Identity Platform",
    program_id: "program-digital",
    program_name: "Digital Platform Program",
    verdict: "at_risk",
    target: "2026-12-11",
    target_source: "committed",
    p50: "2026-12-04",
    p85: "2026-12-21",
    team_date: "2026-12-09",
    team_key: "IDP-12",
    days_late: 6,
    reason:
      "At risk: 50% likely by 4 Dec, but 85% only by 21 Dec, after the committed date 11 Dec.",
    health: "1 open blocker, all replied",
    health_day: TODAY,
    done: 6,
    total: 15,
    pods: [
      {
        id: "pod-identity",
        name: "Identity Pod",
        done: 6,
        total: 15,
        verdict: "at_risk",
        target: "2026-12-11",
        p85: "2026-12-21",
      },
    ],
    releases: [],
    marks: [],
    plan: null,
    start: "2026-09-06",
    kept_on: TODAY,
    computed_at: COMPUTED,
    reconstructed: false,
  },
  {
    id: "project-insights",
    name: "Customer Insights",
    program_id: "program-digital",
    program_name: "Digital Platform Program",
    verdict: "no_date",
    target: null,
    target_source: null,
    p50: "2027-01-08",
    p85: "2027-01-22",
    team_date: null,
    team_key: null,
    days_late: null,
    reason: "No delivery date is committed yet; likely by 22 Jan 2027 (85%).",
    health: "no open blockers, 1 of 3 not replied",
    health_day: TODAY,
    done: 2,
    total: 9,
    pods: [
      {
        id: "pod-data",
        name: "Data Pod",
        done: 2,
        total: 9,
        verdict: "no_date",
        target: null,
        p85: "2027-01-22",
      },
    ],
    releases: [],
    marks: [],
    plan: null,
    start: "2026-09-06",
    kept_on: TODAY,
    computed_at: COMPUTED,
    reconstructed: false,
  },
];

const FILTER = {
  late: "off_track",
  at_risk: "at_risk",
  no_date: "no_date",
  on_track: "on_track",
  done: "done",
  not_enough_data: "not_enough_data",
};

function delivery(url, roles) {
  const q = (url.searchParams.get("q") ?? "").toLowerCase();
  const wanted = url.searchParams
    .getAll("verdict")
    .map((value) => FILTER[value])
    .filter(Boolean);
  const offset = Number(url.searchParams.get("offset") ?? 0);
  const limit = Number(url.searchParams.get("limit") ?? 50);
  const own = has(roles, "po", "mgr", "exec")
    ? PROJECTS
    : PROJECTS.filter((project) => project.id === "project-checkout");
  const searched = own.filter((project) => !q || project.name.toLowerCase().includes(q));
  const shown = searched.filter((project) => !wanted.length || wanted.includes(project.verdict));
  return {
    as_of: TODAY,
    computed_at: COMPUTED,
    counts: counts(searched.map((project) => project.verdict)),
    total: shown.length,
    offset,
    limit,
    projects: shown.slice(offset, offset + limit),
    programs: [
      {
        id: "program-digital",
        name: "Digital Platform Program",
        counts: counts(searched.map((project) => project.verdict)),
      },
    ],
  };
}

function scope(roles) {
  const executive = !has(roles, "po", "mgr", "sm");
  const baselines = has(roles, "po", "mgr", "exec");
  const listed = has(roles, "po", "mgr", "exec") ? PROJECTS : PROJECTS.slice(0, 1);
  return {
    as_of: TODAY,
    reads_baselines: baselines,
    hygiene_enabled: true,
    counts_only: executive,
    projects: listed.map((project) => ({
      id: project.id,
      name: project.name,
      program_id: project.program_id,
      program_name: project.program_name,
      plan: baselines ? project.plan : null,
      added_keys: baselines && project.plan ? ["CHK-201", "CHK-205", "CHK-206"] : [],
      without_label_keys: baselines && project.plan ? ["CHK-205", "CHK-206"] : [],
      scope_day: TODAY,
      hygiene_open: project.id === "project-checkout" ? 2 : 0,
    })),
    rules: [
      {
        id: "rule-no-label",
        name: "Added after planning without a change label",
        asks: false,
        open: 2,
      },
    ],
    findings: executive
      ? []
      : [
          {
            id: "f-1",
            issue_key: "CHK-205",
            title: "Saved cards",
            rule_id: "rule-no-label",
            rule_name: "Added after planning without a change label",
            project_id: "project-checkout",
            project_name: "Checkout Revamp",
            owner_name: "Mina Patel",
            owner_team: "Payments Pod",
            found_on: "2026-10-02",
            open_days: 4,
            asks: false,
            held: false,
          },
          {
            id: "f-2",
            issue_key: "CHK-206",
            title: "Gift cards",
            rule_id: "rule-no-label",
            rule_name: "Added after planning without a change label",
            project_id: "project-checkout",
            project_name: "Checkout Revamp",
            owner_name: "Mina Patel",
            owner_team: "Payments Pod",
            found_on: "2026-10-05",
            open_days: 1,
            asks: false,
            held: false,
          },
        ],
    findings_total: 2,
  };
}

function warnings(roles) {
  const named = has(roles, "sm", "mgr");
  const lists = has(roles, "sm", "mgr");
  const item = (over) => ({
    since: null,
    age_known: true,
    owner_is_you: false,
    project_id: "project-checkout",
    project_name: "Checkout Revamp",
    severity: "amber",
    evidence_url: null,
    watermelon: false,
    ...over,
    owner: named ? over.owner : null,
  });
  const group = (kind, title, items, countsOnly = false, count = items.length) => ({
    kind,
    title,
    count,
    items: countsOnly ? [] : items,
    counts_only: countsOnly,
  });
  const groups = [
    group(
      "blockers",
      "Blockers people reported",
      [
        item({
          id: "b-1",
          kind: "blocker",
          key: "CHK-101",
          line: "Waiting on finance to confirm refund rounding · waits on Storefront Pod",
          age_days: 12,
          since: "2026-09-24",
          owner: "Ben Sorensen",
          owner_team: "Storefront Pod",
          severity: "red",
        }),
      ],
      !lists,
      1,
    ),
    group("tools_disagree", "Says on track, tools disagree", [
      item({
        id: "d-1",
        kind: "said_in_review_no_mr",
        key: "CHK-104",
        line: "CHK-104 is said to be in review, but no open merge request names it.",
        age_days: 0,
        age_known: false,
        since: TODAY,
        owner: null,
        owner_team: "Payments Pod",
      }),
    ]),
    group("done_in_code", "Done in code, open in Jira", []),
    group("review_stuck", "Review stuck", [
      item({
        id: "r-1",
        kind: "pr_age",
        key: "CHK-102",
        line: "Pull request 'CHK-102 Payment intent API' in demo/checkout-api has been open for 9 days.",
        age_days: 9,
        owner: "Kai Thompson",
        owner_team: "Payments Pod",
      }),
    ]),
    group("no_movement", "No movement", []),
    group("no_code_yet", "Work with no code yet", []),
  ];
  return {
    as_of: TODAY,
    seen_since: "2026-10-01",
    groups,
    total: groups.reduce((sum, item) => sum + item.count, 0),
    projects: 3,
  };
}

export function api(req, url, roles, _userId, send, deny) {
  const p = url.pathname;
  if (p === "/portfolio/delivery") {
    return has(roles, "po", "mgr", "exec", "sm")
      ? (send(200, delivery(url, roles)), true)
      : (deny(), true);
  }
  if (p === "/portfolio/scope") {
    return has(roles, "po", "mgr", "exec", "sm") ? (send(200, scope(roles)), true) : (deny(), true);
  }
  if (p === "/portfolio/warnings") {
    return has(roles, "po", "mgr", "exec", "sm")
      ? (send(200, warnings(roles)), true)
      : (deny(), true);
  }
  return false;
}
