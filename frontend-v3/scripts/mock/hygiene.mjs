// Mock handlers for the Jira hygiene lane: the board (GET /projects/{id}/hygiene,
// GET /pods/{id}/hygiene), Check now, a person's actions on a finding, and the
// configuration under /config/hygiene. NOT real data: shapes follow
// src/api/generated.ts, names follow the seeded demo tenant. Checkout Revamp has
// two rules on: "Added after planning without a change label" (asks whoever
// decides) finds CHK-111 and CHK-112, added since the baseline without the CR
// label; "Story without an estimate" (lists only) finds three stories.
//
// Reading keeps the readiness board's rule: product owner, manager, admin, an
// executive (counts only, never who) and a scrum master for the project their
// pod works on; a developer is refused. Acting takes act_on_readiness (not an
// executive). The configuration is the admin's. Wired from ../mock-api.mjs.
import { roster } from "../mock-console.mjs";

const PROJECT = "project-checkout";
const NOW = "2026-10-06T09:45:00Z";
const nameOf = (id) => roster.find((p) => p.id === id)?.name ?? null;
const PODS = [
  { id: "pod-payments", name: "Payments Pod" },
  { id: "pod-storefront", name: "Storefront Pod" },
];

const matcher = (kind, value) => ({ kind, value });
const condition = (kind, extra = {}) => ({
  kind,
  labels: [],
  field: null,
  gate_kind: "",
  stage: null,
  working_days: 0,
  points_over: 0,
  ...extra,
});
const rule = (id, name, cond, extra = {}) => ({
  rule_id: id,
  version: 1,
  name,
  scope: "project",
  select: { matchers: [], stage_filter: "any", stage: null, every_type: false },
  condition: cond,
  severity: "list_only",
  need: "fix",
  owner: { kind: "pod_scrum_master", profile: "" },
  blocking_before: null,
  suggested_fix: "",
  enabled: true,
  ...extra,
});

const EXAMPLES = [
  rule(
    "added-after-planning-without-change-label",
    "Added after planning without a change label",
    condition("added_after_baseline_without_label"),
    {
      owner: { kind: "decision_owner", profile: "" },
      need: "decision",
      suggested_fix:
        "Label {key} {label} if it was agreed as a change, or take it out of {release}.",
    },
  ),
  rule(
    "story-without-estimate",
    "Story without an estimate",
    condition("missing_field", { field: "story_points" }),
    {
      select: {
        matchers: [matcher("issue_type", "Story")],
        stage_filter: "before",
        stage: "in_development",
        every_type: false,
      },
      suggested_fix: "Estimate {key} before it starts.",
    },
  ),
  rule(
    "requirement-without-acceptance-criteria",
    "Requirement without acceptance criteria",
    condition("missing_gate_kind", { gate_kind: "acceptance" }),
    {
      select: { matchers: [], stage_filter: "before", stage: "in_development", every_type: false },
      owner: { kind: "profile", profile: "product" },
      suggested_fix: "Write {key}'s acceptance criteria under an 'Acceptance criteria' heading.",
    },
  ),
  rule(
    "bug-without-severity",
    "Bug without a severity",
    condition("missing_field", { field: "severity" }),
    {
      select: {
        matchers: [matcher("issue_type", "Bug")],
        stage_filter: "any",
        stage: null,
        every_type: true,
      },
      owner: { kind: "reporter", profile: "" },
      suggested_fix: "Set {key}'s severity.",
    },
  ),
  rule(
    "stale-in-development",
    "No movement in development",
    condition("stale_in_status", { stage: "in_development", working_days: 5 }),
    {
      owner: { kind: "assignee", profile: "" },
      suggested_fix:
        "{key} has had no Jira change for {days} working days in {stage}: move it on, or say what holds it.",
    },
  ),
];

let settings = {
  enabled: true,
  change_labels: ["CR"],
  stale_working_days: 5,
  updated_at: NOW,
  updated_by: "U1001",
};
const rules = [{ ...EXAMPLES[0], severity: "ask" }, EXAMPLES[1]];

const finding = (id, ruleId, key, title, what, owner, extra = {}) => ({
  finding_id: id,
  rule_id: ruleId,
  issue_key: key,
  title,
  scope: { kind: "project", id: PROJECT, name: "Checkout Revamp" },
  stage: "raised",
  what,
  owner,
  suggested_fix: "",
  state: "open",
  held: false,
  first_seen_at: NOW,
  open_days: 0,
  resolution: null,
  decision: null,
  pods: [],
  ...extra,
});
const decider = { name: nameOf("U1003"), via: "decision_owner", profile_label: null };
const ira = { name: nameOf("U1006"), via: "pod_scrum_master", profile_label: null };
const ben = { name: nameOf("U1014"), via: "pod_scrum_master", profile_label: null };

const findings = [
  finding(
    "hf-1",
    "added-after-planning-without-change-label",
    "CHK-111",
    "Wallet top-up",
    "Joined the scope on 29 Sep, after planning on 15 Sep, without the CR label",
    decider,
    {
      suggested_fix:
        "Label CHK-111 CR if it was agreed as a change, or take it out of Checkout Revamp.",
      first_seen_at: "2026-09-29T09:45:00Z",
      open_days: 7,
      pods: ["pod-payments"],
    },
  ),
  finding(
    "hf-2",
    "added-after-planning-without-change-label",
    "CHK-112",
    "Split payments",
    "Joined the scope on 2 Oct, after planning on 15 Sep, without the CR label",
    decider,
    {
      suggested_fix:
        "Label CHK-112 CR if it was agreed as a change, or take it out of Checkout Revamp.",
      first_seen_at: "2026-10-02T09:45:00Z",
      open_days: 4,
      pods: ["pod-payments"],
    },
  ),
  finding("hf-3", "story-without-estimate", "CHK-104", "Saved cards", "No story points", ira, {
    suggested_fix: "Estimate CHK-104 before it starts.",
    first_seen_at: "2026-10-01T09:45:00Z",
    open_days: 5,
    pods: ["pod-payments"],
  }),
  finding("hf-4", "story-without-estimate", "CHK-106", "Card art", "No story points", ira, {
    suggested_fix: "Estimate CHK-106 before it starts.",
    pods: ["pod-payments"],
  }),
  finding(
    "hf-5",
    "story-without-estimate",
    "CHK-120",
    "Product page reviews",
    "No story points",
    ben,
    {
      suggested_fix: "Estimate CHK-120 before it starts.",
      stage: "groomed",
      pods: ["pod-storefront"],
    },
  ),
];

const ownPods = (userId) => roster.find((p) => p.id === userId)?.pods ?? [];

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

function stated(item) {
  const kind = item.condition.kind;
  const tail = item.severity === "ask" ? "asks its owner" : "lists them";
  if (kind === "added_after_baseline_without_label") {
    return `Requirements added after planning without the planning's change label; ${tail}.`;
  }
  if (kind === "missing_field") {
    const where = item.select?.stage ? " before In development" : "";
    return `Story issues${where} without story points; ${tail}.`;
  }
  return `${item.name}; ${tail}.`;
}

function view(f, roles) {
  const may = roles.includes("admin") || roles.some((r) => ["mgr", "po", "sm"].includes(r));
  const open = f.state === "open";
  // eslint-disable-next-line no-unused-vars -- `pods` is the mock's own filter field.
  const { pods, ...rest } = f;
  return {
    ...rest,
    can: {
      waive: may && open,
      not_applicable: may && open,
      recheck: may && open,
      reopen: may && !open && f.state !== "resolved",
    },
  };
}

function board(roles, userId, podId) {
  const countsOnly =
    roles.includes("exec") && !roles.some((r) => ["admin", "mgr", "po", "sm"].includes(r));
  const isSm = roles.includes("sm") && !roles.some((r) => ["admin", "mgr", "po"].includes(r));
  const pods = isSm ? PODS.filter((pod) => ownPods(userId).includes(pod.id)) : PODS;
  const enabled = rules.filter((item) => item.enabled);
  const shown = findings.filter(
    (f) =>
      f.state !== "resolved" &&
      enabled.some((item) => item.rule_id === f.rule_id) &&
      (!podId || f.pods.includes(podId)),
  );
  const rows = enabled.map((item) => {
    const mine = shown.filter((f) => f.rule_id === item.rule_id);
    const open = mine.filter((f) => f.state === "open");
    return {
      rule_id: item.rule_id,
      name: item.name,
      stated: stated(item),
      severity: item.severity,
      need: item.need,
      condition: item.condition.kind,
      blocking: Boolean(item.blocking_before),
      open: open.length,
      new_today: open.filter((f) => f.open_days === 0).length,
      oldest_days: open.length ? Math.max(...open.map((f) => f.open_days)) : null,
      held: 0,
      decided: mine.length - open.length,
    };
  });
  return {
    project_id: PROJECT,
    pod_id: podId || null,
    release_id: null,
    scope_name: "Checkout Revamp",
    scan: {
      enabled: settings.enabled,
      last_run_at: NOW,
      last_run_status: "ok",
      data_as_of: "2026-10-06T09:00:00Z",
      stale: false,
    },
    counts_only: countsOnly,
    open: rows.reduce((sum, row) => sum + row.open, 0),
    held: 0,
    rules: rows,
    findings: countsOnly ? [] : shown.map((f) => view(f, roles)),
    pods: countsOnly ? [] : pods,
    releases: [{ id: "rel-1-0", name: "Release 1.0" }],
    can_run: !countsOnly && settings.enabled,
  };
}

function preview(body) {
  const kind = body?.rule?.condition?.kind;
  const scope = { kind: "project", id: body?.project_id ?? PROJECT, name: "Checkout Revamp" };
  const issues =
    kind === "added_after_baseline_without_label"
      ? findings.filter((f) => f.rule_id.startsWith("added-after"))
      : findings.filter((f) => f.rule_id === "story-without-estimate");
  return {
    scopes: [
      {
        scope,
        looked_at: 24,
        found: issues.length,
        issues: issues.map((f) => ({
          key: f.issue_key,
          title: f.title,
          what: f.what,
          owner_name: f.owner.name,
          suggested_fix: f.suggested_fix,
        })),
        needs_baseline: false,
      },
    ],
  };
}

/** Handles the call and returns true, or returns false for the next handler. */
// eslint-disable-next-line no-unused-vars -- `deny` is part of the lane handler signature.
export function api(req, url, roles, userId, send, deny) {
  const p = url.pathname;
  const method = req.method ?? "GET";
  const has = (...r) => roles.includes("admin") || roles.some((x) => r.includes(x));
  const reply = (status, body) => {
    send(status, body);
    return true;
  };
  const board_ = p.match(/^\/projects\/([^/]+)\/hygiene(\/run)?$/);
  if (board_) {
    if (!has("po", "mgr", "exec", "sm")) {
      return reply(403, {
        detail: "Release readiness is read by the people who decide on a release.",
      });
    }
    if (board_[1] !== PROJECT) {
      return reply(200, {
        ...board(roles, userId, ""),
        project_id: board_[1],
        rules: [],
        findings: [],
        open: 0,
      });
    }
    const podId = url.searchParams.get("pod_id") || "";
    if (board_[2]) {
      if (method !== "POST") return false;
      if (!has("po", "mgr", "sm")) {
        return reply(403, {
          detail:
            "You can act on Jira hygiene only for pods you run and the projects they work on.",
        });
      }
      const now = board(roles, userId, podId);
      return reply(200, {
        run: { status: "ok", scopes: 4, opened: 0, resolved: 0, changed: 0, open: now.open },
        board: now,
      });
    }
    return reply(200, board(roles, userId, podId));
  }
  if (/^\/pods\/[^/]+\/hygiene$/.test(p)) {
    if (!has("mgr", "sm")) {
      return reply(403, {
        detail: "You can act on readiness only for pods you run and the projects they work on.",
      });
    }
    return reply(200, { ...board(roles, userId, ""), project_id: null });
  }
  const action = p.match(
    /^\/hygiene-findings\/([^/]+)\/(waive|not-applicable|reopen|recheck|history)$/,
  );
  if (action) {
    const f = findings.find((item) => item.finding_id === action[1]);
    if (!f) return reply(404, { detail: `No finding '${action[1]}'.` });
    if (action[2] === "history") {
      return reply(200, {
        entries: [{ at: f.first_seen_at, action: "opened", actor_name: "OpenProgram", note: null }],
      });
    }
    if (!has("po", "mgr", "sm")) {
      return reply(403, {
        detail: "You can act on Jira hygiene only for pods you run and the projects they work on.",
      });
    }
    if (action[2] === "recheck" || action[2] === "reopen") {
      if (action[2] === "reopen") Object.assign(f, { state: "open", decision: null });
      return reply(200, view(f, roles));
    }
    void readBody(req).then((body) => {
      const reason = String(body.reason ?? "").trim();
      if (reason.length < 3) return send(422, { detail: "Give a reason of 3 to 300 characters." });
      if (f.state !== "open") {
        return send(409, { detail: "Only an open finding is waived or set aside." });
      }
      Object.assign(f, {
        state: action[2] === "waive" ? "waived" : "not_applicable",
        decision: { by_name: nameOf(userId), at: new Date().toISOString(), reason },
      });
      send(200, view(f, roles));
    });
    return true;
  }
  if (p.startsWith("/config/hygiene")) {
    if (!roles.includes("admin"))
      return reply(403, { detail: `${userId} is not authorized for manage_config` });
    if (p === "/config/hygiene" && method === "GET") {
      const added = new Set(rules.map((item) => item.rule_id));
      return reply(200, {
        settings,
        is_default: false,
        rules,
        examples: EXAMPLES.filter((item) => !added.has(item.rule_id)),
        gate_kinds: [
          { key: "acceptance", label: "Acceptance criteria" },
          { key: "test_case", label: "Test cases" },
        ],
        placeholders: [
          "key",
          "title",
          "stage",
          "release",
          "label",
          "field",
          "days",
          "baseline_date",
        ],
      });
    }
    if (p === "/config/hygiene/settings" && method === "PUT") {
      void readBody(req).then((body) => {
        settings = {
          ...settings,
          ...body,
          updated_at: new Date().toISOString(),
          updated_by: userId,
        };
        send(200, settings);
      });
      return true;
    }
    if (p === "/config/hygiene/rules" && method === "PUT") {
      void readBody(req).then((body) => {
        const id =
          body.rule_id ||
          String(body.name ?? "rule")
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, "-")
            .replace(/^-|-$/g, "");
        const at = rules.findIndex((item) => item.rule_id === id);
        const saved = { ...body, rule_id: id, version: at >= 0 ? rules[at].version + 1 : 1 };
        if (at >= 0) rules[at] = saved;
        else rules.push(saved);
        send(200, saved);
      });
      return true;
    }
    const removed = p.match(/^\/config\/hygiene\/rules\/([^/]+)$/);
    if (removed && method === "DELETE") {
      const at = rules.findIndex((item) => item.rule_id === removed[1]);
      if (at < 0) return reply(404, { detail: `No rule '${removed[1]}'.` });
      rules.splice(at, 1);
      return reply(204, null);
    }
    if (p === "/config/hygiene/rules/preview" && method === "POST") {
      void readBody(req).then((body) => send(200, preview(body)));
      return true;
    }
  }
  return false;
}
