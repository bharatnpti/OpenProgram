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

import * as consoleData from "./mock-console.mjs";
import * as todayMock from "./mock/today.mjs";
import * as adminStructure from "./mock/admin-structure.mjs";
import * as reportsLane from "./mock/reports.mjs";
import * as shellMock from "./mock/shell.mjs";
import * as adminConfig from "./mock/admin-config.mjs";
import * as flowMock from "./mock/flow.mjs";
import * as redesignMock from "./mock/redesign.mjs";
import * as assistantMock from "./mock/assistant.mjs";
import * as datesMock from "./mock/dates.mjs";

const DIST = path.resolve(process.argv[2] ?? new URL("../dist", import.meta.url).pathname);
const PORT = Number(process.env.PORT ?? 5175);
const TODAY = "2026-10-06";

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
  console_path: "/reports/project-checkout/daily?report=rep-checkout",
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
const can = {
  aggregate: (r) => r.some((x) => ["sm", "po", "mgr", "exec", "admin"].includes(x)),
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
  // The admin-config mock answers first. It owns /config/branding, every /config/escalation
  // read and write (Overall's included), the sync status and the directory sync.
  if (adminConfig.api(req, url, roles, req.headers["x-openprogram-dev-user"], send, deny)) return;
  if (p === "/api/v1/auth/status")
    return send(200, {
      authenticated: true,
      provider: "dev",
      login_url: null,
      demo_mode: true,
      chat_enabled: true,
      user: null,
    });
  if (p === "/api/v1/auth/dev-users") return send(200, { items: consoleData.roster });
  if (p === "/projects") return send(200, consoleData.projects);
  if (p === "/portfolio/risks")
    return can.aggregate(roles)
      ? send(200, { as_of: TODAY, risks: risks.risks, drift: risks.drift })
      : deny();
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
            console_path: "/reports/project-checkout/daily?report=rep-checkout-r1",
            rag: "amber",
          }
        : preview,
    );
  if (/^\/day-reports\/[^/]+\/runs$/.test(p)) return send(200, p.includes("-r1") ? [] : runs);
  if (p === "/projects/project-checkout/risks")
    return can.aggregate(roles) ? send(200, risks) : deny();
  const userId = req.headers["x-openprogram-dev-user"] ?? "U1001";
  // The shell mock goes first: of the reads the lane mocks share, it answers
  // only past-day ones (as_of), which the today mock would answer as today.
  if (shellMock.api(req, url, roles, userId, send, deny)) return;
  if (datesMock.api(req, url, roles, userId, send, deny)) return;
  if (assistantMock.api(req, url, roles, userId, send, deny)) return;
  if (todayMock.api(req, url, roles, userId, send, deny)) return;
  if (adminStructure.api(req, url, roles, userId, send, deny)) return;
  if (reportsLane.api(req, url, roles, userId, send, deny)) return;
  if (flowMock.api(req, url, roles, userId, send, deny)) return;
  if (redesignMock.api(req, url, roles, userId, send, deny)) return;
  const handled = consoleData.consoleApi(req, url, roles, userId, send, deny);
  if (handled !== false) return handled;
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
    // A file in the build is served as is; a page navigation gets the app;
    // anything else is an API call.
    let file = path.join(DIST, url.pathname);
    const isFile = file.startsWith(DIST) && fs.existsSync(file) && fs.statSync(file).isFile();
    if (!isFile && !req.headers.accept?.includes("text/html")) return api(req, res, url);
    if (!isFile) file = path.join(DIST, "index.html");
    res.writeHead(200, { "content-type": TYPES[path.extname(file)] ?? "application/octet-stream" });
    fs.createReadStream(file).pipe(res);
  })
  .listen(PORT, "127.0.0.1", () => console.log(`mock on ${PORT}`));
