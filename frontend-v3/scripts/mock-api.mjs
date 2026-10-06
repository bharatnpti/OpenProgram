// Mock OpenProgram API for running frontend-v3 without the backend, and for
// the screenshots in docs/screenshots. NOT real data: response shapes follow
// src/api/generated.ts, names and story follow the seeded demo tenant
// (scripts/demo_roster.py, docs/ops/local-demo.md). Only Checkout Revamp has
// reports, forecast, requirements, gates and risks; the other projects are empty.
//
// It honours the dev acting-as headers like the real backend: x-openprogram-dev-roles
// decides 403s and the can_send / can_write_note flags.
//
//   cd frontend-v3
//   npm run mock          # builds with a same-origin API, serves http://127.0.0.1:5175
//
// Pick a person in the header (Asha Rao manager/admin, Elena Fischer exec,
// Mina Patel PO, Ira Novak SM, Kai Thompson dev) to see each role.
import http from "node:http";
import fs from "node:fs";
import path from "node:path";

const DIST = path.resolve(process.argv[2] ?? new URL("../dist", import.meta.url).pathname);
const PORT = Number(process.env.PORT ?? 5175);
const TODAY = "2026-10-06";

const people = [
  {
    id: "U1011",
    name: "Elena Fischer",
    title: "Director of Engineering",
    roles: ["exec"],
    pods: [],
  },
  {
    id: "U1001",
    name: "Asha Rao",
    title: "Engineering Manager",
    roles: ["mgr", "admin"],
    pods: ["pod-payments", "pod-storefront", "pod-identity", "pod-data"],
  },
  {
    id: "U1003",
    name: "Mina Patel",
    title: "Product Owner",
    roles: ["po"],
    pods: ["pod-payments"],
  },
  { id: "U1006", name: "Ira Novak", title: "Scrum Master", roles: ["sm"], pods: ["pod-payments"] },
  {
    id: "U1007",
    name: "Kai Thompson",
    title: "Backend Engineer",
    roles: ["dev"],
    pods: ["pod-payments"],
  },
];

const dir = (id, name, rag, pods) => ({
  id,
  kind: "project",
  name,
  description: null,
  code: null,
  metadata: {},
  rag,
  source: "confirmed",
  program_ids: ["program-digital"],
  project_ids: [],
  workstream_ids: [],
  pod_ids: pods,
  member_ids: [],
  task_ids: [],
  people: [],
  in_use: true,
});
const projects = [
  dir("project-checkout", "Checkout Revamp", "red", ["pod-payments", "pod-storefront"]),
  dir("project-identity", "Identity Platform", "amber", ["pod-identity"]),
  dir("project-insights", "Customer Insights", "amber", ["pod-data"]),
];

const run = (id, day, trigger, status, actor) => ({
  run_id: id,
  report_id: "rep-checkout",
  report_date: day,
  trigger,
  status,
  started_at: `${day}T15:30:0${id.length % 9}Z`,
  finished_at: `${day}T15:30:12Z`,
  title: `Checkout Revamp: day report, ${day}`,
  actor: actor?.[0] ?? null,
  actor_name: actor?.[1] ?? null,
  outcomes: [
    {
      kind: "chat_channel",
      target: "#checkout-delivery",
      label: "#checkout-delivery",
      ok: true,
      detail: "Posted.",
    },
    { kind: "person", target: "U1003", label: "Mina Patel", ok: true, detail: "Sent." },
    { kind: "person", target: "U1001", label: "Asha Rao", ok: true, detail: "Sent." },
    {
      kind: "email",
      target: "checkout-leads@acme.example",
      label: "checkout-leads@acme.example",
      ok: status !== "partial",
      detail: status === "partial" ? "The mail server refused the message (550)." : "Sent.",
    },
  ],
});
const runs = [
  run("r6", "2026-10-05", "schedule", "sent"),
  run("r5", "2026-10-02", "schedule", "partial"),
  run("r4", "2026-10-02", "manual", "sent", ["U1006", "Ira Novak"]),
  run("r3", "2026-10-01", "schedule", "sent"),
  run("r2", "2026-09-30", "schedule", "sent"),
];

function dayReports(roles) {
  const setup = roles.some((r) => ["sm", "mgr", "admin"].includes(r));
  const note = roles.some((r) => ["po", "mgr", "admin"].includes(r));
  const base = {
    project_id: "project-checkout",
    project_name: "Checkout Revamp",
    enabled: true,
    schedule: { local_time: "17:30", timezone: "Europe/Berlin", weekdays: [0, 1, 2, 3, 4] },
    destinations: setup
      ? [
          { kind: "chat_channel", target: "#checkout-delivery" },
          { kind: "person", target: "U1003" },
          { kind: "person", target: "U1001" },
          { kind: "email", target: "checkout-leads@acme.example" },
        ]
      : [],
    destination_count: 4,
    audience: [
      { kind: "chat_channel", count: 1, names: [] },
      { kind: "person", count: 2, names: ["Mina Patel", "Asha Rao"] },
      { kind: "email", count: 1, names: [] },
    ],
    audience_summary: "1 chat channel, 2 people by direct message, 1 email address",
    updated_at: "2026-09-28T08:00:00Z",
    updated_by: "U1006",
    can_send: setup,
    can_edit: setup,
    can_write_note: note,
  };
  return [
    {
      ...base,
      report_id: "rep-checkout",
      name: "Checkout Revamp: end of day",
      release_id: null,
      release_name: null,
      last_run: runs[0],
      note: {
        report_id: "rep-checkout",
        report_date: TODAY,
        text: "Refund decision lands Thursday; the 3-D Secure sandbox is the critical path for 30 Oct.",
        author: "U1003",
        author_name: "Mina Patel",
        updated_at: `${TODAY}T11:02:00Z`,
      },
    },
    {
      ...base,
      report_id: "rep-checkout-r1",
      name: "Checkout Revamp, Release 1.0: end of day",
      release_id: "rel-1-0",
      release_name: "Release 1.0",
      last_run: null,
      note: null,
    },
  ];
}

const preview = {
  title: "Checkout Revamp: day report, Tue 6 Oct 2026",
  report_date: TODAY,
  rag: "red",
  headline: "3 fixes, 1 decision, 1 answer and 1 review needed, 2 escalated.",
  percent_complete: 58.2,
  progress_line: "58% complete: 99 of 170 story points in production (55% on Mon 5 Oct 2026).",
  console_url: null,
  text: "",
  sections: [
    {
      title: "In short",
      empty_text: "",
      lines: [
        "Mina Patel: Refund decision lands Thursday; the 3-D Secure sandbox is the critical path for 30 Oct.",
        "Delivery Fri 30 Oct 2026: off track. History says 85% likely by Thu 12 Nov 2026. The team's latest date is Tue 3 Nov 2026 (CHK-103).",
        "Needed most: a fix from Omar Haddad on CHK-103 (9 days) and a decision from Mina Patel on CHK-102 (7 days).",
      ],
      groups: [],
    },
    {
      title: "Where we stand",
      empty_text: "",
      lines: [],
      groups: [
        {
          heading: "Progress",
          lines: [
            "58% complete: 99 of 170 story points in production (55% on Mon 5 Oct 2026).",
            "Raised 2 (−1), groomed 4 (+1), in development 6 (−1), in testing 3 (+1), business testing 2 (−1), production 7 (+1).",
          ],
        },
        {
          heading: "What changed since Mon 5 Oct 2026",
          lines: [
            "CHK-104 Capture retry runbook: business testing → production.",
            "CHK-109 Cart promo stacking: raised → groomed.",
            "CHK-101 Payment intent API: in development → in testing.",
          ],
        },
        {
          heading: "Acceptance and tests",
          lines: [
            "Business acceptance: 7 of 24 passed, 2 waiting for sign-off, 5 suggestions waiting for a person.",
            "Engineering delivery: 9 of 24 passed; 41 test cases, 3 failed.",
          ],
        },
      ],
    },
    {
      title: "Most important",
      empty_text: "Nothing threatens the delivery date today.",
      groups: [],
      lines: [
        "The forecast and the committed date disagree by more than five working days.",
        "Payments Pod plans its part for Tue 3 Nov 2026, later than the project.",
        "CHK-102 reached business testing without Engineering delivery passing.",
        "3-D Secure step-up has had no state change in 9 day(s).",
      ],
    },
    {
      title: "What we need, and from whom",
      empty_text: "Nothing is needed from anyone today.",
      lines: [],
      groups: [
        {
          heading: "Omar Haddad",
          lines: [
            "Fix: Sandbox credentials for 3-D Secure, blocks CHK-103 (9 days; PLT-88 on Platform). Escalated to Asha Rao (manager).",
            "Answer: Which ACS simulator do we certify the challenge flow against? (5 days; CHK-103).",
          ],
        },
        {
          heading: "Mina Patel",
          lines: [
            "Decision: Refund edge cases, separate ledger entry or not (7 days; CHK-102). Escalated to Asha Rao (manager).",
          ],
        },
        {
          heading: "Noah Weber",
          lines: [
            "Review: Payment intent capture path, MR !214 (4 days; CHK-101). Escalated to Ira Novak (scrum master).",
          ],
        },
        {
          heading: "Sofia Bergmann",
          lines: [
            "Fix: Test case TC-402 partial refund rounding failed (1 day; CHK-102).",
            "Fix: Test case TC-417 3-tender refund failed (1 day; CHK-102).",
          ],
        },
      ],
    },
    {
      title: "Open questions",
      empty_text: "No open questions.",
      lines: [],
      groups: [],
      table: {
        columns: ["Ticket", "What we asked", "Asked to", "Asked on", "Heard back?"],
        rows: [
          [
            "CHK-112",
            "Maximum tenders per order for a gift-card split?",
            "Mina Patel",
            "Tue 6 Oct 2026",
            "Not yet (today)",
          ],
          [
            "CHK-102",
            "Do partial refunds need a separate ledger entry?",
            "Mina Patel",
            "Tue 29 Sep 2026",
            "Not yet (7 days)",
          ],
          [
            "CHK-103",
            "Which ACS simulator do we certify the challenge flow against?",
            "Omar Haddad",
            "Thu 1 Oct 2026",
            "Partly",
          ],
          [
            "CHK-101",
            "Is a 24-hour idempotency key TTL acceptable?",
            "Mina Patel",
            "Fri 2 Oct 2026",
            "Yes",
          ],
        ],
      },
    },
  ],
};

const commitment = {
  target_date: "2026-10-30",
  original_date: "2026-10-23",
  times_moved: 1,
  moved_days: 7,
  changes: [
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
};
const scope = (kind, id, name, target, verdict, reasons, history, team, total, open) => ({
  scope_kind: kind,
  scope_id: id,
  project_id: "project-checkout",
  name,
  commitment:
    kind === "project"
      ? commitment
      : { ...commitment, target_date: target, times_moved: 0, moved_days: null, changes: [] },
  target,
  target_source: "committed",
  jira_release_date: kind === "project" ? "2026-10-30" : null,
  history,
  team,
  verdict,
  reasons,
  total,
  open,
});
const delivery = {
  project: scope(
    "project",
    "project-checkout",
    "Checkout Revamp",
    "2026-10-30",
    "off_track",
    [
      "Committed by Mina Patel; moved once, by 7 days.",
      "The completion rate says Wed 4 Nov (50%) to Thu 12 Nov (85%): 3 to 9 working days late.",
      "The team's own dates say Tue 3 Nov (CHK-103).",
      "Payments Pod plans its part for Tue 3 Nov, later than the project.",
    ],
    {
      p50: "2026-11-04",
      p85: "2026-11-12",
      remaining: 17,
      unit: "requirements",
      sample_days: 21,
      completed_in_sample: 6,
      reason: null,
    },
    { latest: "2026-11-03", latest_key: "CHK-103", dated: 14, undated: 3 },
    24,
    17,
  ),
  pods: [
    scope(
      "pod",
      "pod-payments",
      "Payments Pod",
      "2026-11-03",
      "off_track",
      ["Planned later than the project's 30 Oct."],
      {
        p50: "2026-11-05",
        p85: "2026-11-13",
        remaining: 11,
        unit: "requirements",
        sample_days: 21,
        completed_in_sample: 3,
        reason: null,
      },
      { latest: "2026-11-03", latest_key: "CHK-103", dated: 9, undated: 2 },
      15,
      11,
    ),
    scope(
      "pod",
      "pod-storefront",
      "Storefront Pod",
      "2026-10-16",
      "on_track",
      ["Six of seven planned items merged."],
      {
        p50: "2026-10-14",
        p85: "2026-10-16",
        remaining: 6,
        unit: "requirements",
        sample_days: 21,
        completed_in_sample: 3,
        reason: null,
      },
      { latest: "2026-10-15", latest_key: "CHK-109", dated: 5, undated: 1 },
      9,
      6,
    ),
  ],
  releases: [
    scope(
      "release",
      "rel-1-0",
      "Release 1.0",
      "2026-10-30",
      "at_risk",
      ["2 of its 11 requirements are blocked."],
      {
        p50: "2026-10-29",
        p85: "2026-11-04",
        remaining: 6,
        unit: "requirements",
        sample_days: 21,
        completed_in_sample: 4,
        reason: null,
      },
      { latest: "2026-10-28", latest_key: "CHK-101", dated: 8, undated: 1 },
      11,
      6,
    ),
  ],
};

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
  timeline.push({ day: d.toISOString().slice(0, 10), counts });
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
const requirements = {
  project_id: "project-checkout",
  project_name: "Checkout Revamp",
  as_of: TODAY,
  release_id: null,
  release_name: null,
  live: true,
  available: true,
  total: 24,
  done: 7,
  percent_complete: 58.2,
  has_points: true,
  points_total: 170,
  points_done: 99,
  stages: STAGES.map((s, k) => ({
    stage: s,
    label: LABELS[s],
    count: end[k],
    points: [6, 21, 34, 18, 13, 99][k],
    change: [-1, 1, -1, 1, -1, 1][k],
  })),
  timeline,
  moves: [
    {
      key: "CHK-104",
      title: "Capture retry runbook",
      from_stage: "business_testing",
      to_stage: "production",
    },
    { key: "CHK-109", title: "Cart promo stacking", from_stage: "raised", to_stage: "groomed" },
    {
      key: "CHK-101",
      title: "Payment intent API",
      from_stage: "in_development",
      to_stage: "in_testing",
    },
  ],
  previous_day: "2026-10-05",
  unmapped_statuses: ["Backlog"],
  excluded: 1,
  requirements: reqRows.map(([key, title, stage, status, who, pts, since]) => ({
    key,
    title,
    stage,
    status,
    mapped: status !== "Backlog",
    assignee_name: who,
    story_points: pts,
    due_date: null,
    in_stage_since: since,
  })),
};

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
const ev = (id, state, met, total, suggested = 0, missing = []) => ({
  template_id: id,
  state,
  met,
  total,
  suggested,
  missing_kinds: missing,
});
const issue = (key, title, stage, status, biz, eng, without = []) => ({
  key,
  title,
  stage,
  status,
  evaluations: [biz, eng],
  items: [],
  passed_without: without,
});
const gates = {
  project_id: "project-checkout",
  release_id: null,
  templates,
  actor_names: {},
  issues: [
    issue(
      "CHK-104",
      "Capture retry runbook",
      "production",
      "Done",
      ev("business-acceptance", "passed", 2, 2),
      ev("engineering-delivery", "passed", 6, 6),
    ),
    issue(
      "CHK-102",
      "Refund edge cases",
      "business_testing",
      "UAT",
      ev("business-acceptance", "open", 3, 4),
      ev("engineering-delivery", "failed", 8, 10),
      ["Engineering delivery"],
    ),
    issue(
      "CHK-101",
      "Payment intent API",
      "in_testing",
      "In QA",
      ev("business-acceptance", "open", 3, 4),
      ev("engineering-delivery", "passed", 12, 12),
    ),
    issue(
      "CHK-103",
      "3-D Secure step-up",
      "in_development",
      "Blocked",
      ev("business-acceptance", "missing", 0, 0, 3, ["Acceptance criterion"]),
      ev("engineering-delivery", "open", 0, 8),
    ),
    issue(
      "CHK-109",
      "Cart promo stacking",
      "groomed",
      "Ready for dev",
      ev("business-acceptance", "open", 0, 2),
      ev("engineering-delivery", "open", 0, 4),
    ),
  ],
  questions: [
    {
      question_id: "q1",
      issue_key: "CHK-112",
      comment_ref: "c1",
      asked_by: "U1002",
      asked_by_name: "Liam Chen",
      asked_to: "U1003",
      asked_to_name: "Mina Patel",
      asked_at: "2026-10-06T09:40:00Z",
      summary: "Maximum tenders per order for a gift-card split?",
      status: "not_yet",
      confirmed: true,
      status_set_by_person: false,
      answered_ref: null,
    },
    {
      question_id: "q2",
      issue_key: "CHK-102",
      comment_ref: "c2",
      asked_by: "U1004",
      asked_by_name: "Noah Weber",
      asked_to: "U1003",
      asked_to_name: "Mina Patel",
      asked_at: "2026-09-29T13:10:00Z",
      summary: "Do partial refunds need a separate ledger entry?",
      status: "not_yet",
      confirmed: true,
      status_set_by_person: false,
      answered_ref: null,
    },
    {
      question_id: "q3",
      issue_key: "CHK-103",
      comment_ref: "c3",
      asked_by: "U1007",
      asked_by_name: "Kai Thompson",
      asked_to: "U1008",
      asked_to_name: "Omar Haddad",
      asked_at: "2026-10-01T10:05:00Z",
      summary: "Which ACS simulator do we certify the challenge flow against?",
      status: "partly",
      confirmed: true,
      status_set_by_person: true,
      answered_ref: "c9",
    },
    {
      question_id: "q4",
      issue_key: "CHK-101",
      comment_ref: "c4",
      asked_by: "U1002",
      asked_by_name: "Liam Chen",
      asked_to: "U1003",
      asked_to_name: "Mina Patel",
      asked_at: "2026-10-02T08:30:00Z",
      summary: "Is a 24-hour idempotency key TTL acceptable?",
      status: "answered",
      confirmed: true,
      status_set_by_person: false,
      answered_ref: "c5",
    },
    {
      question_id: "q5",
      issue_key: "CHK-109",
      comment_ref: "c6",
      asked_by: "U1012",
      asked_by_name: "Tom Okafor",
      asked_to: "U1003",
      asked_to_name: "Mina Patel",
      asked_at: "2026-09-30T15:00:00Z",
      summary: "Can the promo-stacking rules from the epic be linked here?",
      status: "closed_unanswered",
      confirmed: false,
      status_set_by_person: false,
      answered_ref: null,
    },
  ],
};

const ref = (kind, id) => ({ tenant_id: "demo", kind, id });
const risks = {
  project_id: "project-checkout",
  as_of: TODAY,
  risks: [
    {
      rule_id: "stale_work_item",
      severity: "red",
      entity_ref: ref("work_item", "CHK-103"),
      workstream_id: "ws-payments",
      reason: "3-D Secure step-up has had no state change in 9 day(s).",
      evidence: { identifier: "CHK-103", url: null, url_is_user_supplied: false },
      age_days: 9,
      detected_at: "2026-10-01T06:00:00Z",
      status: "open",
      owner_id: "U1007",
      owner_status_summary: "Wired the challenge flow. Blocked on sandbox credentials.",
      owner_status_source: "confirmed",
      owner_status_as_of: TODAY,
      owner_status_has_blockers: true,
      is_watermelon: false,
      person_name: "Kai Thompson",
    },
    {
      rule_id: "aging_pull_request",
      severity: "amber",
      entity_ref: ref("pull_request", "!214"),
      workstream_id: "ws-payments",
      reason: "Pull request !214 has been open for 4 day(s).",
      evidence: { identifier: "MR !214", url: null, url_is_user_supplied: false },
      age_days: 4,
      detected_at: "2026-10-04T06:00:00Z",
      status: "open",
      owner_id: "U1002",
      owner_status_summary: "Payment intent API handlers done, wiring the capture path next.",
      owner_status_source: "confirmed",
      owner_status_as_of: TODAY,
      owner_status_has_blockers: true,
      is_watermelon: false,
      person_name: "Liam Chen",
    },
    {
      rule_id: "stale_work_item",
      severity: "amber",
      entity_ref: ref("work_item", "CHK-102"),
      workstream_id: "ws-payments",
      reason: "Refund edge cases has had no state change in 7 day(s).",
      evidence: { identifier: "CHK-102", url: null, url_is_user_supplied: false },
      age_days: 7,
      detected_at: "2026-10-03T06:00:00Z",
      status: "open",
      owner_id: "U1004",
      owner_status_summary: "Waiting on the ledger decision.",
      owner_status_source: "partial",
      owner_status_as_of: TODAY,
      owner_status_has_blockers: true,
      is_watermelon: false,
      person_name: "Noah Weber",
    },
  ],
  drift: [
    {
      kind: "progress_without_activity",
      severity: "amber",
      entity_ref: ref("work_item", "CHK-105"),
      workstream_id: "ws-cart",
      reason: "Reported progressing, but no commits or pull requests for 6 days.",
      detected_at: "2026-10-05T06:00:00Z",
      owner_id: "U1012",
      stated_source: "confirmed",
      evidence: { identifier: "CHK-105", url: null, url_is_user_supplied: false },
      child_entity_ref: null,
    },
  ],
};
const escalation = {
  project_id: "project-checkout",
  source: "tenant",
  decision_owner_id: "U1003",
  updated_at: "2026-09-01T09:00:00Z",
  updated_by: "U1001",
  levels: [
    {
      label: "Scrum master",
      source: "team_scrum_master",
      member_id: null,
      after_days: { fix: 2, decision: 2, answer: 3, review: 2 },
    },
    {
      label: "Manager",
      source: "team_manager",
      member_id: null,
      after_days: { fix: 4, decision: 4, answer: 6, review: 5 },
    },
    {
      label: "Director",
      source: "member",
      member_id: "U1011",
      after_days: { fix: 10, decision: 8 },
    },
  ],
};

const can = {
  progress: (r) => r.some((x) => ["po", "mgr", "exec", "admin"].includes(x)),
  aggregate: (r) => r.some((x) => ["sm", "po", "mgr", "exec", "admin"].includes(x)),
  config: (r) => r.includes("admin"),
};

function api(req, res, url) {
  const roles = (req.headers["x-openprogram-dev-roles"] ?? "").split(",").filter(Boolean);
  const send = (status, body) => {
    res.writeHead(status, { "content-type": "application/json" });
    res.end(JSON.stringify(body));
  };
  const deny = () =>
    send(403, { detail: `Role ${roles.join(",") || "none"} lacks this capability.` });
  const p = url.pathname;
  if (p === "/api/v1/auth/status")
    return send(200, {
      authenticated: true,
      provider: "dev",
      login_url: null,
      demo_mode: true,
      chat_enabled: false,
      user: null,
    });
  if (p === "/api/v1/auth/dev-users") return send(200, { items: people });
  if (p === "/projects") return send(200, projects);
  if (p === "/day-reports") {
    const pid = url.searchParams.get("project_id");
    return send(200, pid && pid !== "project-checkout" ? [] : dayReports(roles));
  }
  if (/^\/day-reports\/[^/]+\/preview$/.test(p))
    return send(
      200,
      p.includes("-r1")
        ? {
            ...preview,
            title: "Checkout Revamp, Release 1.0: day report, Tue 6 Oct 2026",
            rag: "amber",
          }
        : preview,
    );
  if (/^\/day-reports\/[^/]+\/runs$/.test(p)) return send(200, p.includes("-r1") ? [] : runs);
  if (p === "/projects/project-checkout/delivery")
    return can.progress(roles) ? send(200, delivery) : deny();
  if (p === "/projects/project-checkout/requirements")
    return can.progress(roles) ? send(200, requirements) : deny();
  if (p === "/projects/project-checkout/gates") return send(200, gates);
  if (p === "/projects/project-checkout/risks")
    return can.aggregate(roles) ? send(200, risks) : deny();
  if (p === "/config/escalation/projects/project-checkout")
    return can.config(roles) ? send(200, escalation) : deny();
  return send(404, { detail: "Not found." });
}

const TYPES = {
  ".js": "text/javascript",
  ".css": "text/css",
  ".html": "text/html",
  ".svg": "image/svg+xml",
  ".ttf": "font/ttf",
  ".png": "image/png",
};
http
  .createServer((req, res) => {
    const url = new URL(req.url, `http://127.0.0.1:${PORT}`);
    const isApi =
      url.pathname.startsWith("/api/") ||
      (/^\/(projects|day-reports|config)(\/|$)/.test(url.pathname) &&
        !req.headers.accept?.includes("text/html"));
    if (isApi) return api(req, res, url);
    let file = path.join(DIST, url.pathname);
    if (!file.startsWith(DIST) || !fs.existsSync(file) || fs.statSync(file).isDirectory())
      file = path.join(DIST, "index.html");
    res.writeHead(200, { "content-type": TYPES[path.extname(file)] ?? "application/octet-stream" });
    fs.createReadStream(file).pipe(res);
  })
  .listen(PORT, "127.0.0.1", () => console.log(`mock on ${PORT}`));
