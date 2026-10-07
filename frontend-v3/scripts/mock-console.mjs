// Mock data for the console screens (Today, Delivery, Signals, Coordination,
// Chat, Admin), used by scripts/mock-api.mjs. NOT real data: shapes follow
// src/api/generated.ts, names and story follow the seeded demo tenant.

const TODAY = "2026-10-06";
const at = (time, day = TODAY) => `${day}T${time}:00Z`;
const ref = (kind, id) => ({ tenant_id: "demo", kind, id });

export const roster = [
  ["U1011", "Elena Fischer", "Director of Engineering", ["exec"], []],
  [
    "U1001",
    "Asha Rao",
    "Engineering Manager",
    ["mgr", "admin"],
    ["pod-payments", "pod-storefront", "pod-identity", "pod-data"],
  ],
  ["U1003", "Mina Patel", "Product Owner", ["po"], ["pod-payments"]],
  ["U1013", "Hana Kobayashi", "Product Owner", ["po"], ["pod-identity"]],
  ["U1006", "Ira Novak", "Scrum Master", ["sm"], ["pod-payments"]],
  ["U1014", "Ben Sorensen", "Scrum Master", ["sm"], ["pod-storefront"]],
  ["U1002", "Liam Chen", "Platform Engineer", ["dev"], ["pod-payments"]],
  ["U1004", "Noah Weber", "Senior Backend Engineer", ["dev"], ["pod-payments"]],
  ["U1007", "Kai Thompson", "Backend Engineer", ["dev"], ["pod-payments"]],
  ["U1005", "Zoe Almeida", "Frontend Engineer", ["dev"], ["pod-storefront"]],
  ["U1012", "Tom Okafor", "Frontend Engineer", ["dev"], ["pod-storefront"]],
  ["U1009", "Sofia Bergmann", "QA Engineer", ["dev"], ["pod-payments"]],
  ["U1008", "Omar Haddad", "SRE", ["dev"], ["pod-identity"]],
  ["U1010", "Raj Iyer", "Data Engineer", ["dev"], ["pod-data"]],
].map(([id, name, title, roles, pods]) => ({ id, name, title, email: null, roles, pods }));
const nameOf = (id) => roster.find((p) => p.id === id)?.name ?? id;
const membersOf = (pod) =>
  roster.filter((p) => p.pods.includes(pod) && !p.roles.includes("mgr")).map((p) => p.id);

const item = (kind, id, name, rag, extra = {}) => ({
  id,
  kind,
  name,
  description: null,
  code: null,
  metadata: {},
  rag,
  source: rag === "unknown" ? "unknown" : "confirmed",
  program_ids: [],
  project_ids: [],
  workstream_ids: [],
  pod_ids: [],
  member_ids: [],
  task_ids: [],
  people: [],
  in_use: true,
  ...extra,
});
const person = (key, id) => ({ key, id, member_id: id, name: nameOf(id) });

export const programs = [
  item("program", "program-digital", "Digital Platform Program", "red", {
    project_ids: ["project-checkout", "project-identity", "project-insights"],
  }),
];
export const projects = [
  item("project", "project-checkout", "Checkout Revamp", "red", {
    program_ids: ["program-digital"],
    workstream_ids: ["ws-payments", "ws-cart"],
    pod_ids: ["pod-payments", "pod-storefront"],
  }),
  item("project", "project-identity", "Identity Platform", "amber", {
    program_ids: ["program-digital"],
    workstream_ids: ["ws-login", "ws-sso"],
    pod_ids: ["pod-identity"],
  }),
  item("project", "project-insights", "Customer Insights", "amber", {
    program_ids: ["program-digital"],
    workstream_ids: ["ws-datalake"],
    pod_ids: ["pod-data"],
  }),
];
const ws = (id, name, rag, project, pods, meta, people) =>
  item("workstream", id, name, rag, {
    project_ids: [project],
    pod_ids: pods,
    metadata: meta,
    people,
  });
export const workstreams = [
  ws(
    "ws-payments",
    "Payments API",
    "red",
    "project-checkout",
    ["pod-payments"],
    { type: "build", phase: "delivery", target_date: "2026-10-30" },
    [person("owner_id", "U1003"), person("sm_id", "U1006")],
  ),
  ws(
    "ws-cart",
    "Cart & Pricing",
    "green",
    "project-checkout",
    ["pod-storefront"],
    { type: "build", phase: "delivery", target_date: "2026-10-16" },
    [person("owner_id", "U1003"), person("sm_id", "U1014")],
  ),
  ws(
    "ws-login",
    "Login Experience",
    "amber",
    "project-identity",
    ["pod-identity"],
    { type: "build", phase: "testing", target_date: "2026-11-06" },
    [person("owner_id", "U1013")],
  ),
  ws(
    "ws-sso",
    "Enterprise SSO",
    "amber",
    "project-identity",
    ["pod-identity"],
    { type: "spike", phase: "discovery" },
    [person("owner_id", "U1013")],
  ),
  ws(
    "ws-datalake",
    "Data Lake Ingest",
    "amber",
    "project-insights",
    ["pod-data"],
    { type: "build", phase: "delivery", target_date: "2026-11-20" },
    [],
  ),
];
const pod = (id, name, rag, projectIds, wsIds) =>
  item("pod", id, name, rag, {
    project_ids: projectIds,
    workstream_ids: wsIds,
    member_ids: membersOf(id),
  });
export const pods = [
  pod("pod-payments", "Payments Pod", "red", ["project-checkout"], ["ws-payments"]),
  pod("pod-storefront", "Storefront Pod", "green", ["project-checkout"], ["ws-cart"]),
  pod("pod-identity", "Identity Pod", "amber", ["project-identity"], ["ws-login", "ws-sso"]),
  pod("pod-data", "Data Pod", "amber", ["project-insights"], ["ws-datalake"]),
];

const factor = (description, contributes, kind, id, k = "blocker") => ({
  description,
  contributes,
  source_ref: ref(kind, id),
  kind: k,
  blocker_id: null,
  work_item_ref: null,
  unattributed: false,
  applies_to_pod_ids: [],
});

const blockerDetail = (id, description, wi, wiName, age) => ({
  blocker_id: id,
  description,
  work_item_id: wi,
  work_item_name: wiName,
  pod_id: "pod-payments",
  unattributed: false,
  first_seen_on: new Date(Date.parse(at("09:00")) - age * 864e5).toISOString().slice(0, 10),
  age_days: age,
});

export function myStatus(userId) {
  if (userId === "U1007") {
    return {
      source: "unknown",
      developer_confirmed: false,
      summary: "",
      blockers: ["Sandbox credentials for 3-D Secure"],
      blocker_details: [
        blockerDetail(
          "b-sandbox",
          "Sandbox credentials for 3-D Secure",
          "CHK-103",
          "3-D Secure step-up",
          9,
        ),
      ],
      eta_change_days: null,
      status_as_of: TODAY,
      confirmed_at: null,
    };
  }
  return {
    source: "confirmed",
    developer_confirmed: true,
    summary: "Payment intent API handlers done, wiring the capture path next.",
    blockers: ["Payment intent PR needs a second reviewer"],
    blocker_details: [
      blockerDetail(
        "b-review",
        "Payment intent PR needs a second reviewer",
        "CHK-101",
        "Payment intent API",
        4,
      ),
    ],
    eta_change_days: null,
    status_as_of: TODAY,
    confirmed_at: at("07:20"),
  };
}

export function focus(userId) {
  return {
    developer_id: userId,
    developer_name: nameOf(userId),
    as_of: TODAY,
    status_source: "confirmed",
    developer_confirmed: true,
    status_as_of: TODAY,
    summary: "",
    blockers: [],
    blocker_details: [],
    tasks: [
      {
        id: "CHK-104",
        name: "Capture retry runbook",
        rag: "green",
        source: "confirmed",
        confidence: 0.92,
        deadline: null,
      },
      {
        id: "CHK-101",
        name: "Ship payment intent API review fixes",
        rag: "amber",
        source: "confirmed",
        confidence: 0.81,
        deadline: "2026-10-09",
      },
    ],
    focus: [
      {
        kind: "blocker",
        label: "Payment intent PR needs a second reviewer",
        source: "confirmed",
        source_ref: ref("work_item", "CHK-101"),
        confidence: null,
        deadline: null,
      },
      {
        kind: "task",
        label: "Ship payment intent API review fixes",
        source: "confirmed",
        source_ref: ref("task", "CHK-101"),
        confidence: 0.81,
        deadline: "2026-10-09",
      },
    ],
  };
}

const request = (id, requester, counterpart, kind, status, note, day, delivery = "sent") => ({
  id,
  requester_id: requester,
  counterpart_id: counterpart,
  counterpart_display_name: nameOf(counterpart),
  counterpart_email: null,
  kind,
  status,
  note,
  raw_name: null,
  source_correlation_id: `c-${id}`,
  created_at: at("08:00", day),
  updated_at: at("08:00", day),
  delivery,
});
export const requests = [
  request("r1", "U1004", "U1002", "review", "open", "Capture retry path, MR !214", "2026-10-05"),
  request(
    "r2",
    "U1004",
    "U1003",
    "input",
    "acknowledged",
    "Refund edge cases: separate ledger entry or not",
    "2026-09-29",
  ),
  request(
    "r3",
    "U1007",
    "U1008",
    "dependency",
    "open",
    "Sandbox credentials for 3-D Secure (PLT-88)",
    "2026-09-27",
    "retrying",
  ),
  request("r4", "U1012", "U1006", "input", "open", "Sprint scope for cart pricing", "2026-10-04"),
  request(
    "r5",
    "U1002",
    "U1003",
    "input",
    "needs_resolution",
    "Ask “the payments lead” about TTL",
    "2026-10-02",
    "not_delivered",
  ),
];
export function myRequests(userId, relation) {
  const list = requests.filter((r) =>
    relation === "raised" ? r.requester_id === userId : r.counterpart_id === userId,
  );
  return { requests: list };
}

export function podCheckins(podId) {
  const states = {
    U1002: ["confirmed", "Payment intent API handlers done"],
    U1004: ["confirmed", "Refund edge cases waiting on the ledger decision"],
    U1007: ["missing", ""],
    U1005: ["partial", "ETA not answered · follow-up sent"],
    U1009: ["confirmed", "Regression run on CHK-102"],
    U1012: ["confirmed", "Promo stacking groomed"],
    U1008: ["confirmed", "PLT-88 queued"],
    U1010: ["stale", "Attribution spike paused"],
  };
  const developers = membersOf(podId)
    .filter((id) => states[id] || true)
    .map((id) => {
      const [state, summary] = states[id] ?? ["confirmed", "On track"];
      return {
        developer_id: id,
        developer_name: nameOf(id),
        state,
        source:
          state === "missing"
            ? "unknown"
            : state === "partial"
              ? "partial"
              : state === "stale"
                ? "stale"
                : "confirmed",
        status_as_of: state === "missing" ? null : at("07:30"),
        summary,
      };
    });
  const count = (s) => developers.filter((d) => d.state === s).length;
  return {
    pod_id: podId,
    pod_name: pods.find((p) => p.id === podId)?.name ?? podId,
    as_of: TODAY,
    confirmed: count("confirmed"),
    partial: count("partial"),
    stale: count("stale"),
    missing: count("missing"),
    developers,
  };
}

const blocker = (id, description, age, owner, wi) => ({
  id,
  blocker_id: id,
  description,
  age_days: age,
  owner_id: owner,
  owner_name: nameOf(owner),
  source: "confirmed",
  status_as_of: TODAY,
  source_ref: ref("developer", owner),
  work_item_ref: wi ? ref("work_item", wi) : null,
  pod_ref: ref("pod", "pod-payments"),
  unattributed: false,
  first_seen_on: new Date(Date.parse(at("09:00")) - age * 864e5).toISOString().slice(0, 10),
});
export function podBlockers(podId) {
  const list =
    podId === "pod-payments"
      ? [
          blocker("b1", "Sandbox credentials for 3-D Secure", 9, "U1007", "CHK-103"),
          blocker("b2", "Payment intent PR needs a second reviewer", 4, "U1002", "CHK-101"),
          blocker("b3", "Refund edge cases wait on a product decision", 2, "U1004", "CHK-102"),
        ]
      : [];
  return {
    pod_id: podId,
    pod_name: pods.find((p) => p.id === podId)?.name ?? podId,
    as_of: TODAY,
    blockers: list,
  };
}
export function podRollup(podId) {
  const p = pods.find((x) => x.id === podId);
  const factors =
    podId === "pod-payments"
      ? [
          factor("Two blockers open past 7 days", "red", "developer", "U1007"),
          factor("One member has not confirmed today", "unknown", "developer", "U1007", "stale"),
          factor("Payment intent PR waits on a reviewer", "amber", "developer", "U1002"),
        ]
      : [factor("Every member confirmed; no open blockers", "green", "pod", podId, "checkins")];
  return {
    pod_id: podId,
    pod_name: p?.name ?? podId,
    as_of: TODAY,
    rag: p?.rag ?? "unknown",
    source: "confirmed",
    factors,
    source_names: { U1007: "Kai Thompson", U1002: "Liam Chen" },
  };
}
export function podTasks(podId) {
  const t = (id, name, rag, owners, blocked, bl = [], status = "In Progress") => ({
    id,
    name,
    rag,
    source: "confirmed",
    confidence: 0.8,
    deadline: null,
    owners: owners.map((o) => ({ id: o, name: nameOf(o) })),
    blocked,
    open_blockers: bl,
    tracker_status: status,
  });
  if (podId !== "pod-payments") return { pod_id: podId, pod_name: podId, as_of: TODAY, tasks: [] };
  return {
    pod_id: podId,
    pod_name: "Payments Pod",
    as_of: TODAY,
    tasks: [
      t(
        "CHK-103",
        "3-D Secure step-up",
        "red",
        ["U1007"],
        true,
        [
          {
            blocker_id: "b1",
            description: "Sandbox credentials",
            first_seen_on: "2026-09-27",
            age_days: 9,
          },
        ],
        "Blocked",
      ),
      t("CHK-101", "Payment intent API", "amber", ["U1002"], false, [], "In QA"),
      t("CHK-102", "Refund edge cases", "amber", ["U1004", "U1009"], false, [], "UAT"),
      t("CHK-104", "Capture retry runbook", "green", ["U1002"], false, [], "Done"),
    ],
  };
}

const taskP = (id, name, rag, status, deadline = null) => ({
  id,
  name,
  rag,
  source: "confirmed",
  confidence: 0.8,
  deadline,
  tracker_status: status,
});
const checkoutTasks = [
  taskP("CHK-103", "3-D Secure step-up", "red", "Blocked", "2026-10-23"),
  taskP("CHK-102", "Refund edge cases", "amber", "UAT"),
  taskP("CHK-101", "Payment intent API", "amber", "In QA", "2026-10-09"),
  taskP("CHK-105", "Cart price breakdown", "amber", "In Progress"),
  taskP("CHK-112", "Gift card split tender", "unknown", "Backlog"),
  taskP("CHK-104", "Capture retry runbook", "green", "Done"),
  taskP("CHK-099", "Saved cards list", "green", "Done"),
  taskP("CHK-100", "Payment method picker", "green", "Done"),
];
const progressOf = (id, name, rag, tasks, factors) => {
  const n = (r) => tasks.filter((t) => t.rag === r).length;
  return {
    as_of: TODAY,
    rag,
    source: "confirmed",
    confidence: 0.81,
    percent_complete: 58.2,
    total_tasks: tasks.length,
    green_tasks: n("green"),
    amber_tasks: n("amber"),
    red_tasks: n("red"),
    unknown_tasks: n("unknown"),
    factors,
    source_names: { U1007: "Kai Thompson" },
    tasks,
    ...id,
    ...name,
  };
};
export function projectProgress(projectId) {
  const p = projects.find((x) => x.id === projectId);
  const tasks =
    projectId === "project-checkout"
      ? checkoutTasks
      : checkoutTasks.slice(4, 7).map((t) => ({ ...t, id: t.id.replace("CHK", "IDN") }));
  return progressOf(
    { project_id: projectId },
    { project_name: p?.name ?? projectId },
    p?.rag ?? "unknown",
    tasks,
    [
      factor("3-D Secure step-up is blocked on the critical path", "red", "developer", "U1007"),
      factor("Refund edge cases wait on a product decision", "amber", "developer", "U1004"),
    ],
  );
}
export function workstreamProgress(wsId) {
  const w = workstreams.find((x) => x.id === wsId);
  return progressOf(
    { workstream_id: wsId },
    { workstream_name: w?.name ?? wsId },
    w?.rag ?? "unknown",
    checkoutTasks.slice(0, 4),
    [
      factor("3-D Secure step-up is blocked on the critical path", "red", "developer", "U1007"),
      factor("Target date is in 24 days", "amber", "workstream", wsId, "target_date"),
    ],
  );
}

export function programTree(programId) {
  const node = (n, factors = []) => ({
    id: n.id,
    kind: n.kind,
    name: n.name,
    rag: n.rag,
    source: "confirmed",
    confidence: 0.8,
    factors,
  });
  const nodes = [
    node(programs[0], [
      factor(
        "Checkout Revamp is red: 3-D Secure step-up blocked 9 days",
        "red",
        "project",
        "project-checkout",
        "child",
      ),
      factor(
        "Identity Platform is amber on an aging review",
        "amber",
        "project",
        "project-identity",
        "child",
      ),
    ]),
    ...projects.map((p) =>
      node(
        p,
        p.id === "project-checkout"
          ? [
              factor(
                "Payments Pod is red: two blockers past 7 days",
                "red",
                "pod",
                "pod-payments",
                "child",
              ),
            ]
          : [
              factor(
                p.id === "project-identity"
                  ? "Enterprise SSO review open 21 days"
                  : "Attribution spike stale 9 days",
                "amber",
                "workstream",
                p.workstream_ids[0],
                "child",
              ),
            ],
      ),
    ),
    ...workstreams.map((w) => node(w)),
    ...pods.map((p) => node(p)),
    ...roster
      .filter((r) => r.roles.includes("dev"))
      .map((r) => ({
        id: r.id,
        kind: "developer",
        name: r.name,
        rag: "green",
        source: "confirmed",
        confidence: 0.8,
        factors: [],
      })),
  ];
  const edges = [
    ...projects.map((p) => ({ from_node_id: programId, to_node_id: p.id, kind: "contains" })),
    ...workstreams.map((w) => ({
      from_node_id: w.project_ids[0],
      to_node_id: w.id,
      kind: "contains",
    })),
    ...pods.map((p) => ({ from_node_id: p.project_ids[0], to_node_id: p.id, kind: "contains" })),
  ];
  return { root_id: programId, as_of: TODAY, nodes, edges };
}

export const attention = {
  as_of: TODAY,
  program_id: "program-digital",
  rag: "red",
  headline: "Digital Platform Program is at risk",
  detail:
    "Oldest open risk: 3-D Secure step-up has had no state change in 9 days (Checkout Revamp).",
  checkins: { people: 11, asked: 11, answered: 9, first_asked_at: at("07:00") },
  signals: [
    {
      kind: "stale_work_item",
      severity: "red",
      title: "3-D Secure step-up has had no state change in 9 days",
      age_days: 9,
      link: { kind: "project", id: "project-checkout" },
    },
    {
      kind: "aging_pull_request",
      severity: "amber",
      title: "Enterprise SSO review has been open for 21 days",
      age_days: 21,
      link: { kind: "workstream", id: "ws-sso" },
    },
    {
      kind: "stale_work_item",
      severity: "amber",
      title: "Attribution model v2 has had no state change in 9 days",
      age_days: 9,
      link: { kind: "project", id: "project-insights" },
    },
  ],
};
export function trend(id) {
  const points = Array.from({ length: 30 }, (_, i) => {
    const day = new Date(Date.UTC(2026, 8, 7 + i)).toISOString().slice(0, 10);
    const score = i < 3 ? 0 : i < 12 ? 3 : i < 20 ? 2 : 1;
    return {
      as_of: day,
      rag: ["unknown", "red", "amber", "green"][score],
      source: "confirmed",
      score,
    };
  });
  return {
    entity_ref: ref("program", id),
    window_days: 30,
    start: points[0].as_of,
    end: TODAY,
    points,
  };
}
export const briefs = [
  {
    kind: "exec",
    scope_id: "program-digital",
    title: "This week across the portfolio",
    generated_at: at("06:00", "2026-10-05"),
    body: "",
    sources: ["f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9"],
    verdict: "at_risk",
    bullets: [
      "Checkout Revamp stayed red: two Payments blockers are past seven days and one member has not confirmed since Thursday.",
      "Identity Platform is amber on an enterprise SSO review open 21 days.",
      "Storefront merged six of seven planned items.",
      "Customer Insights holds amber on a stale attribution spike.",
    ],
  },
  {
    kind: "weekly_project",
    scope_id: "project-checkout",
    title: "Checkout Revamp, week of 28 Sep",
    generated_at: at("06:00", "2026-10-05"),
    body: "Seven of 24 requirements are in production. The 3-D Secure step-up remains blocked on sandbox credentials, and refunds wait on a ledger decision.",
    sources: ["f1", "f2", "f3"],
    bullets: [],
  },
  {
    kind: "daily_pod",
    scope_id: "pod-payments",
    title: "Payments Pod, Tue 6 Oct",
    generated_at: at("16:00"),
    body: "Five of six confirmed. Three blockers open; the oldest is nine days. CHK-101 moved to testing.",
    sources: ["f1", "f2"],
    bullets: [],
  },
];
export const flow = {
  as_of: TODAY,
  active_count: 17,
  features_in_flight: 9,
  completed_count: 7,
  stale_count: 3,
  abandoned_count: 1,
  avg_cycle_time_days: 6.4,
  avg_pr_age_days: 3.1,
  workstreams: workstreams.map((w, i) => ({
    workstream_id: w.id,
    workstream_name: w.name,
    active_count: [6, 4, 3, 2, 2][i],
    features_in_flight: [3, 2, 2, 1, 1][i],
    completed_count: [3, 4, 0, 0, 0][i],
    stale_count: [1, 0, 0, 1, 1][i],
    abandoned_count: [0, 0, 0, 1, 0][i],
    avg_cycle_time_days: [7.2, 4.1, 6.0, null, 8.3][i],
    avg_pr_age_days: [4.0, 1.5, 21.0, null, 2.2][i],
  })),
};
export const feed = {
  as_of: TODAY,
  since: "2026-09-29",
  items: [
    [
      "check_in",
      "checkin",
      "Liam Chen confirmed: payment intent handlers done",
      at("07:20"),
      "Liam Chen",
    ],
    [
      "vcs",
      "pull_request_opened",
      "MR !214 opened: capture retry path",
      at("16:10", "2026-10-05"),
      "Noah Weber",
    ],
    [
      "issue_tracker",
      "transition",
      "CHK-104 moved to Done",
      at("15:02", "2026-10-05"),
      "Liam Chen",
    ],
    [
      "risk",
      "risk_opened",
      "Refund edge cases has had no state change in 7 days",
      at("06:00", "2026-10-03"),
      null,
    ],
    [
      "request",
      "request_acknowledged",
      "Mina Patel acknowledged: refund ledger decision",
      at("11:40", "2026-10-02"),
      "Mina Patel",
    ],
  ].map(([source, kind, summary, observed_at, person_name]) => ({
    source,
    kind,
    summary,
    entity_ref: ref("project", "project-checkout"),
    observed_at,
    details: {},
    person_name,
  })),
};
export function askAnswer(question) {
  return {
    answer: `Checkout Revamp is at risk because the Payments API workstream is red: the 3-D Secure step-up (CHK-103) has been blocked for 9 days on sandbox credentials from the Platform team. Identity Platform does not depend on the payments API.\n\n(Mock answer to: “${question}”)`,
    references: ["project-checkout", "ws-payments"],
    tools_used: ["graph_search", "open_risks"],
    trace_id: "mock",
    sources: [
      { id: "project-checkout", kind: "project", label: "Checkout Revamp" },
      { id: "ws-payments", kind: "workstream", label: "Payments API" },
      { id: "CHK-103", kind: "work_item", label: "3-D Secure step-up" },
    ],
  };
}

export function chatMessages(userId) {
  const m = (id, direction, text, time, purpose = null, thread = null, day = TODAY) => ({
    tenant_id: "demo",
    message_id: id,
    channel_id: `D-${userId}`,
    user_id: userId,
    direction,
    text,
    created_at: at(time, day),
    correlation_id: null,
    purpose,
    reply_to_message_id: null,
    thread_id: thread,
    metadata: {},
  });
  return {
    items: [
      m(
        "m1",
        "bot",
        "Morning! Quick check-in on CHK-101 (Payment intent API): done, or still going? Anything blocking you?",
        "07:00",
        "checkin",
        null,
        "2026-10-05",
      ),
      m(
        "m2",
        "user",
        "Handlers done, wiring the capture path. Need a second reviewer on MR !214.",
        "07:20",
        null,
        null,
        "2026-10-05",
      ),
      m(
        "m3",
        "bot",
        "Noah Weber asked you for a review: capture retry path, MR !214.",
        "16:12",
        "cross_person_request",
        null,
        "2026-10-05",
      ),
      m("m4", "user", "On it this morning.", "07:05", null, "m3"),
      m(
        "m5",
        "bot",
        "Morning! CHK-101 moved to In QA yesterday. Is the capture path merged? Still waiting on a reviewer?",
        "07:00",
        "checkin",
      ),
    ],
  };
}

export const syncStatus = {
  generated_at: at("09:00"),
  sources: [
    {
      source: "issue_tracker",
      provider: "jira",
      simulated: false,
      sync_enabled: true,
      schedule: "*/15 * * * *",
      stale_after_minutes: 60,
      target_origin: "config",
      health: "healthy",
      last_synced_at: at("08:45"),
      last_attempt_at: at("08:45"),
      last_error: null,
      newest_item_at: at("08:31"),
      config_error: null,
      provider_error: null,
      targets: [
        {
          scope: "project",
          label: "CHK · Checkout Revamp",
          detail: "board 12",
          configured: true,
          health: "healthy",
          last_synced_at: at("08:45"),
          last_attempt_at: at("08:45"),
          last_outcome: "ok",
          last_error: null,
          items_synced: 24,
        },
        {
          scope: "project",
          label: "IDN · Identity Platform",
          detail: null,
          configured: true,
          health: "healthy",
          last_synced_at: at("08:45"),
          last_attempt_at: at("08:45"),
          last_outcome: "ok",
          last_error: null,
          items_synced: 11,
        },
      ],
    },
    {
      source: "vcs",
      provider: "gitlab",
      simulated: false,
      sync_enabled: true,
      schedule: "*/15 * * * *",
      stale_after_minutes: 60,
      target_origin: "config",
      health: "stale",
      last_synced_at: at("06:10"),
      last_attempt_at: at("08:45"),
      last_error: "GitLab answered 502 for checkout/payments-api.",
      newest_item_at: at("05:58"),
      config_error: null,
      provider_error: null,
      targets: [
        {
          scope: "repo",
          label: "checkout/payments-api",
          detail: null,
          configured: true,
          health: "failing",
          last_synced_at: at("06:10"),
          last_attempt_at: at("08:45"),
          last_outcome: "error",
          last_error: "502 Bad Gateway",
          items_synced: 0,
        },
      ],
    },
    {
      source: "calendar",
      provider: "google",
      simulated: true,
      sync_enabled: true,
      schedule: null,
      stale_after_minutes: null,
      target_origin: "config",
      health: "healthy",
      last_synced_at: at("07:00"),
      last_attempt_at: at("07:00"),
      last_error: null,
      newest_item_at: null,
      config_error: null,
      provider_error: null,
      targets: [],
    },
    {
      source: "directory",
      provider: "slack",
      simulated: false,
      sync_enabled: true,
      schedule: "0 6 * * *",
      stale_after_minutes: 1440,
      target_origin: "config",
      health: "healthy",
      last_synced_at: at("06:00"),
      last_attempt_at: at("06:00"),
      last_error: null,
      newest_item_at: null,
      config_error: null,
      provider_error: null,
      targets: [],
    },
  ],
};
const defaults = {
  local_time: "07:00",
  timezone: "Europe/Berlin",
  weekdays: [0, 1, 2, 3, 4],
  reply_wait_seconds: 7200,
  final_reply_wait_seconds: 18000,
};
export const configMembers = roster
  .filter((r) => !r.roles.includes("exec"))
  .map((r) => ({ id: r.id, kind: "developer", name: r.name, metadata: {} }));
export const checkinPreferences = configMembers.map((m) => {
  const custom =
    {
      U1007: { weekdays: [0, 1, 2, 3], timezone: "Europe/London", reply_wait_seconds: 10800 },
      U1010: { timezone: "Asia/Kolkata" },
      U1005: { weekdays: [1, 2, 3, 4] },
    }[m.id] ?? {};
  const inherited = [
    "local_time",
    "timezone",
    "weekdays",
    "reply_wait_seconds",
    "final_reply_wait_seconds",
  ].filter((f) => !(f in custom));
  return { developer_id: m.id, ...defaults, ...custom, inherited, defaults };
});
export const consent = (id) => ({
  developer_id: id,
  consent: { U1007: "auto_apply", U1010: "never" }[id] ?? "always_ask",
});
const cfg = (list, kind) =>
  list.map((x) => ({ id: x.id, kind, name: x.name, code: null, metadata: {} }));
export const config = {
  programs: cfg(programs, "program"),
  projects: cfg(projects, "project"),
  workstreams: cfg(workstreams, "workstream"),
  pods: cfg(pods, "pod"),
};
export const reportSetup = {
  projects: projects.map((p) => ({
    id: p.id,
    name: p.name,
    releases: p.id === "project-checkout" ? [{ release_id: "rel-1-0", name: "1.0" }] : [],
  })),
  people: roster.map((p) => ({ id: p.id, name: p.name })),
  destinations: [
    { kind: "chat_channel", label: "Chat channel", available: true, note: "" },
    { kind: "person", label: "Direct message", available: true, note: "" },
    { kind: "email", label: "Email", available: true, note: "" },
    {
      kind: "teams",
      label: "Teams",
      available: false,
      note: "No Teams connection on Admin → Integrations.",
    },
  ],
};

/** Answers a console endpoint, or returns false when the path is not one of these. */
export function consoleApi(req, url, roles, userId, send, deny) {
  const p = url.pathname;
  const has = (...r) => roles.some((x) => r.includes(x));
  const aggregate = () => has("sm", "po", "mgr", "exec", "admin");
  const progress = () => has("po", "mgr", "exec", "admin");
  const podDetail = () => has("sm", "mgr", "admin");
  const portfolio = () => has("mgr", "exec", "admin");
  const admin = () => has("admin");
  let m;
  if (p === "/programs") return send(200, programs);
  if (p === "/pods") return send(200, pods);
  if (p === "/workstreams") return send(200, workstreams);
  if ((m = p.match(/^\/projects\/([^/]+)\/workstreams$/)))
    return send(
      200,
      workstreams.filter((w) => w.project_ids.includes(m[1])),
    );
  if (p === "/me/status") return send(200, myStatus(userId));
  if (p === "/me/status/confirm" || p === "/me/status/correct")
    return send(200, { ...myStatus(userId), developer_confirmed: true, source: "confirmed" });
  if (p === "/me/focus") return send(200, focus(userId));
  if (p === "/me/cross-person-requests")
    return send(200, myRequests(userId, url.searchParams.get("relation")));
  if (p === "/portfolio/cross-person-requests")
    return aggregate() ? send(200, { requests }) : deny();
  if ((m = p.match(/^\/cross-person-requests\/([^/]+)\/status$/)))
    return send(200, requests.find((r) => r.id === m[1]) ?? requests[0]);
  if ((m = p.match(/^\/pods\/([^/]+)\/(checkins|blockers|rollup|tasks)$/))) {
    if (!podDetail()) return deny();
    return send(
      200,
      { checkins: podCheckins, blockers: podBlockers, rollup: podRollup, tasks: podTasks }[m[2]](
        m[1],
      ),
    );
  }
  if ((m = p.match(/^\/projects\/([^/]+)\/progress$/)))
    return progress() ? send(200, projectProgress(m[1])) : deny();
  if ((m = p.match(/^\/workstreams\/([^/]+)\/progress$/)))
    return progress() ? send(200, workstreamProgress(m[1])) : deny();
  if ((m = p.match(/^\/programs\/([^/]+)\/tree$/)))
    return portfolio() ? send(200, programTree(m[1])) : deny();
  if (p === "/portfolio/attention") return portfolio() ? send(200, attention) : deny();
  if ((m = p.match(/^\/persona\/program\/([^/]+)\/trend$/)))
    return portfolio() ? send(200, trend(m[1])) : deny();
  if (p === "/persona/briefs") {
    if (!aggregate()) return deny();
    const kind = url.searchParams.get("kind");
    const limit = Number(url.searchParams.get("limit") ?? 20);
    return send(200, { briefs: briefs.filter((b) => !kind || b.kind === kind).slice(0, limit) });
  }
  if (p === "/portfolio/flow") return aggregate() ? send(200, flow) : deny();
  if (p === "/portfolio/feed") return aggregate() ? send(200, feed) : deny();
  if (p === "/ask") return aggregate() ? send(200, askAnswer("your question")) : deny();
  if (p === "/test/chat-simulator/status")
    return send(200, {
      enabled: true,
      tenant_id: "demo",
      provider: "mock_slack",
      message_count: 5,
    });
  if (p === "/test/chat-simulator/messages")
    return send(200, chatMessages(url.searchParams.get("user_id") ?? userId));
  if (p.startsWith("/test/chat-simulator/users/"))
    return send(200, {
      message_id: "m9",
      status: "filed",
      processed_message_id: "m9",
      started_checkin: false,
    });
  if (p === "/admin/ops/sync-status") return admin() ? send(200, syncStatus) : deny();
  if (p === "/config/members") return admin() ? send(200, configMembers) : deny();
  if (p === "/config/checkin-preferences") return admin() ? send(200, checkinPreferences) : deny();
  if ((m = p.match(/^\/config\/members\/([^/]+)\/writeback-consent$/)))
    return admin() ? send(200, consent(m[1])) : deny();
  if (p === "/config/tenant/writeback")
    return admin() ? send(200, { enabled: false, source: "default" }) : deny();
  if ((m = p.match(/^\/config\/(programs|projects|workstreams|pods)$/)))
    return admin() ? send(200, config[m[1]]) : deny();
  if (p === "/day-reports/setup") {
    if (!has("sm", "mgr", "admin")) return deny();
    // A manager or admin sets a report up for any project; a scrum master only for the
    // projects of the pods they run, as the real server offers them.
    if (has("mgr", "admin")) return send(200, reportSetup);
    const mine = roster.find((person) => person.id === userId)?.pods ?? [];
    const offered = new Set(
      pods.filter((pod) => mine.includes(pod.id)).flatMap((pod) => pod.project_ids),
    );
    return send(200, {
      ...reportSetup,
      projects: reportSetup.projects.filter((project) => offered.has(project.id)),
    });
  }
  return false;
}
