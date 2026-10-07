// Mock endpoints for the Today, Signals and Coordination lane. NOT real data:
// shapes follow src/api/generated.ts, people and story follow the seeded demo
// tenant (the roster in ../mock-console.mjs). Called by ../mock-api.mjs before
// the generic console router, so what is here wins for these paths.
//
// What it adds beyond the generic mock:
//   - the heat map (/portfolio/heatmap) with each cell's reason and the people in no pod;
//   - a check-in that is an EARLIER day's status carried forward (Kai), and that
//     Confirm and Correct really change, so the round trip can be watched;
//   - request status changes that stick (Acknowledge, Resolve);
//   - pod check-ins with real dates (the API sends a day, not a time);
//   - the team graph (/graph/programs/{id}/tree), which names people for a scrum
//     master or product owner;
//   - more briefs, because a real tenant has dozens;
//   - a second program, when MOCK_PROGRAMS=2 is set (Customer Insights moves to
//     "Platform Operations Program"), to see the program picker. It is red on two
//     different blockers while its teams read amber, as a real program can be: the
//     verdict then leads with the program's own colour (the first program's tiles
//     are red, so its verdict and colour agree).
import * as consoleData from "../mock-console.mjs";

const TODAY = "2026-10-06";
const ref = (kind, id) => ({ tenant_id: "demo", kind, id });
const has = (roles, ...wanted) => roles.some((role) => wanted.includes(role));
const nameOf = (id) => consoleData.roster.find((person) => person.id === id)?.name ?? null;
/** Answers and says so: the router stops at the first handler that returns true. */
const reply = (send, status, body) => {
  send(status, body);
  return true;
};

// ---- a second program, on request -------------------------------------------------

const twoPrograms = process.env.MOCK_PROGRAMS === "2";
const OPS = "program-ops";
if (twoPrograms) {
  const digital = consoleData.programs[0];
  digital.project_ids = digital.project_ids.filter((id) => id !== "project-insights");
  consoleData.projects.find((p) => p.id === "project-insights").program_ids = [OPS];
  consoleData.programs.push({
    ...digital,
    id: OPS,
    name: "Platform Operations Program",
    rag: "red",
    project_ids: ["project-insights"],
  });
}
const projectsOf = (programId) =>
  consoleData.projects.filter((p) => p.program_ids.includes(programId));

// ---- the heat map ---------------------------------------------------------------

const REASONS = {
  "program:program-digital": [
    "Refund edge cases: settle scope with finance blocked",
    "Payments Pod: 2 blockers (Kai, Noah)",
    "Identity Platform: 2 blockers (Sofia, Omar)",
  ],
  "program:program-ops": [
    "2 blockers (Raj, Ben)",
    "Open blocker on INS-502 (Raj Iyer).",
    "Open blocker on INS-504 (Ben Sorensen).",
  ],
  "project:project-checkout": [
    "Refund edge cases: settle scope with finance blocked",
    "3-D Secure step-up has had no state change in 7 days",
  ],
  "project:project-identity": [
    "2 blockers (Sofia, Omar)",
    "A merge request for IDP-301 has been open 13 days",
  ],
  "project:project-insights": ["Blocker on INS-502 (Raj)"],
  "workstream:ws-payments": [
    "Refund edge cases: settle scope with finance blocked",
    "3-D Secure step-up needs attention",
  ],
  "workstream:ws-cart": ["Tasks on track"],
  "workstream:ws-login": ["Passkey enrolment: land review needs attention"],
  "workstream:ws-sso": ["SAML metadata refresh needs attention"],
  "workstream:ws-datalake": ["Attribution model v2 spike needs attention"],
  "pod:pod-payments": [
    "2 blockers (Kai, Noah)",
    "Kai has not confirmed today",
    "Payment intent PR waits on a reviewer",
  ],
  "pod:pod-storefront": ["All 5 confirmed"],
  "pod:pod-identity": ["Blocker on IDP-401 (Omar)", "Blocker on IDP-301 (Sofia)"],
  "pod:pod-data": ["Blocker on INS-502 (Raj)"],
};

function cell(row, entity, rag, name) {
  const reasons = REASONS[`${entity.kind}:${entity.id}`] ?? [];
  return {
    row,
    column: entity.id,
    entity_ref: ref(entity.kind, entity.id),
    rag,
    source: rag === "unknown" ? "unknown" : "confirmed",
    why: reasons[0] ?? "",
    source_ref: ref(entity.kind, entity.id),
    name,
    reason: reasons[0] ?? null,
    reasons,
  };
}

function heatmap(programId) {
  const projects = twoPrograms ? projectsOf(programId) : consoleData.projects;
  const projectIds = new Set(projects.map((p) => p.id));
  const inScope = (item) => !twoPrograms || item.project_ids.some((id) => projectIds.has(id));
  const program = consoleData.programs.find((p) => p.id === programId) ?? consoleData.programs[0];
  const cells = [
    cell("program", program, program.rag, program.name),
    ...projects.map((p) => cell("project", p, p.rag, p.name)),
    ...consoleData.workstreams.filter(inScope).map((w) => cell("workstream", w, w.rag, w.name)),
    ...consoleData.pods.filter(inScope).map((p) => cell("pod", p, p.rag, p.name)),
    ...consoleData.roster
      .filter((person) => person.roles.includes("dev") && person.pods.length > 0)
      .map((person) => ({
        row: "developer",
        column: person.id,
        entity_ref: ref("developer", person.id),
        rag: person.id === "U1007" ? "unknown" : "green",
        source: person.id === "U1007" ? "unknown" : "confirmed",
        why: "Confirmed status has no blockers.",
        source_ref: ref("developer", person.id),
        name: person.name,
        reason: person.id === "U1007" ? "No status reported yet" : "Confirmed, no blockers",
        reasons: [],
      })),
    // The executive is in no pod: a cell of her own on the "no pod" row.
    {
      row: "no pod",
      column: "U1011",
      entity_ref: ref("developer", "U1011"),
      rag: "green",
      source: "confirmed",
      why: "No pod, outside team colours: Confirmed status has no blockers.",
      source_ref: ref("developer", "U1011"),
      name: "Elena Fischer",
      reason: "Confirmed, no blockers",
      reasons: ["Confirmed status has no blockers.", "In no pod, so no team's colour counts it."],
    },
  ];
  return {
    as_of: TODAY,
    rows: [...new Set(cells.map((c) => c.row))],
    columns: [...new Set(cells.map((c) => c.column))],
    cells,
  };
}

function attentionFor(programId) {
  if (programId !== OPS || !twoPrograms) return consoleData.attention;
  return {
    as_of: TODAY,
    program_id: OPS,
    rag: "amber",
    headline: "Amber: Attribution model v2 spike needs attention.",
    detail: "Also: 1 open blocker, Raj Iyer on INS-502.",
    checkins: { people: 3, asked: 3, answered: 2, first_asked_at: `${TODAY}T07:00:00Z` },
    signals: [
      {
        kind: "risk:stale_work_item",
        severity: "amber",
        title: "Attribution model v2 has had no state change in 9 days",
        age_days: 9,
        link: { kind: "project", id: "project-insights" },
      },
    ],
  };
}

// ---- the developer's own check-in -----------------------------------------------

const statuses = new Map();

/** Kai has not answered since 5 Oct: the status on the card is that day's, carried forward. */
function initialStatus(userId) {
  if (userId !== "U1007") return consoleData.myStatus(userId);
  return {
    source: "unknown",
    developer_confirmed: false,
    summary: "No reply to the check-in; status is unknown for this day.",
    blockers: ["Sandbox credentials for 3-D Secure"],
    blocker_details: [
      {
        blocker_id: "b-sandbox",
        description: "Sandbox credentials for 3-D Secure",
        work_item_id: "CHK-103",
        work_item_name: "3-D Secure step-up",
        pod_id: "pod-payments",
        unattributed: false,
        first_seen_on: "2026-09-27",
        age_days: 9,
      },
    ],
    eta_change_days: null,
    status_as_of: "2026-10-05",
    confirmed_at: null,
  };
}
const statusOf = (userId) => statuses.get(userId) ?? initialStatus(userId);

function confirmed(userId, body) {
  const now = statusOf(userId);
  const next = body
    ? {
        ...now,
        source: "confirmed",
        developer_confirmed: true,
        summary: body.summary,
        eta_change_days: body.eta_change_days ?? null,
        blocker_details: (body.blocker_items ?? [])
          .filter((item) => !item.resolved)
          .map((item, i) => {
            const old = now.blocker_details.find((b) => b.blocker_id === item.blocker_id);
            return (
              old ?? {
                blocker_id: `b-new-${Date.now()}-${i}`,
                description: item.description,
                work_item_id: item.work_item_id ?? null,
                work_item_name: null,
                pod_id: item.pod_id ?? null,
                unattributed: !item.work_item_id,
                first_seen_on: TODAY,
                age_days: 0,
              }
            );
          }),
      }
    : {
        ...now,
        source: "confirmed",
        developer_confirmed: true,
        summary:
          now.source === "confirmed" || now.source === "partial"
            ? now.summary
            : `Confirmed the status from ${now.status_as_of}: ${now.summary}`,
      };
  next.blockers = next.blocker_details.map((b) => b.description);
  next.status_as_of = TODAY;
  next.confirmed_at = new Date().toISOString();
  statuses.set(userId, next);
  return next;
}

function focusOf(userId) {
  const status = statusOf(userId);
  const base = consoleData.focus(userId);
  return {
    ...base,
    as_of: TODAY,
    status_source: status.source,
    developer_confirmed: status.developer_confirmed,
    status_as_of: status.status_as_of,
    summary: status.summary,
    blockers: status.blockers,
    blocker_details: status.blocker_details,
    focus: [
      ...status.blocker_details.map((b) => ({
        kind: "blocker",
        label: b.description,
        source: status.source,
        source_ref: ref("developer", userId),
        confidence: null,
        deadline: null,
      })),
      ...base.focus.filter((item) => item.kind !== "blocker"),
    ],
  };
}

// ---- pods: what the API sends is a day, not a time -----------------------------------

function podCheckins(podId) {
  const base = consoleData.podCheckins(podId);
  const developers = base.developers.map((d) => {
    const kai = d.developer_id === "U1007" ? statuses.get("U1007") : null;
    if (kai?.status_as_of === TODAY) {
      return {
        ...d,
        state: "confirmed",
        source: "confirmed",
        status_as_of: TODAY,
        summary: kai.summary,
      };
    }
    if (d.developer_id === "U1008") {
      // Confirmed yesterday, nothing today: stale to the board, though its source says confirmed.
      return { ...d, state: "stale", source: "confirmed", status_as_of: "2026-10-05" };
    }
    return {
      ...d,
      status_as_of: d.state === "missing" ? null : d.state === "stale" ? "2026-10-02" : TODAY,
    };
  });
  const count = (state) => developers.filter((d) => d.state === state).length;
  return {
    ...base,
    confirmed: count("confirmed"),
    partial: count("partial"),
    stale: count("stale"),
    missing: count("missing"),
    developers,
  };
}

// ---- briefs: a real tenant has dozens ---------------------------------------------------

const manyBriefs = [
  ...consoleData.briefs,
  ...Array.from({ length: 9 }, (_, i) => ({
    kind: "daily_pod",
    scope_id: consoleData.pods[i % consoleData.pods.length].id,
    title: `Daily pod summary: ${consoleData.pods[i % consoleData.pods.length].name}`,
    generated_at: `2026-10-0${5 - Math.floor(i / 4)}T16:00:00Z`,
    body: "Most members confirmed their check-in. Two risks remain open without recent progress, and one pull request has been open for six days. A recent commit refactored the guest checkout banner.",
    sources: ["f1", "f2", "f3"],
    verdict: null,
    bullets: [],
  })),
];

// ---- people the team graph names ------------------------------------------------------------

function teamGraph(programId) {
  const program = consoleData.programs.find((p) => p.id === programId) ?? consoleData.programs[0];
  const node = (item) => ({ id: item.id, kind: item.kind, name: item.name, metadata: {} });
  const projects = twoPrograms ? projectsOf(program.id) : consoleData.projects;
  return {
    root: node(program),
    nodes: [
      node(program),
      ...projects.map(node),
      ...consoleData.pods.map(node),
      ...consoleData.roster
        .filter((person) => person.pods.length > 0)
        .map((person) => ({
          id: person.id,
          kind: "developer",
          name: person.name,
          metadata: { chat_external_id: `C-${person.id}` },
        })),
    ],
    edges: [],
  };
}

/** Answers a Today, Signals or Coordination endpoint, or returns false to let the generic mock try. */
export function api(req, url, roles, userId, send, deny) {
  const p = url.pathname;
  const post = req.method === "POST";
  const refuse = () => {
    deny();
    return true;
  };
  const noRecord = () => reply(send, 404, { detail: "status is not available" });
  let m;

  if (p === "/me/status" && req.method === "GET") {
    return nameOf(userId) ? reply(send, 200, statusOf(userId)) : noRecord();
  }
  if (p === "/me/status/confirm" && post) {
    return nameOf(userId) ? reply(send, 200, confirmed(userId, null)) : noRecord();
  }
  if (p === "/me/status/correct" && post) {
    if (!nameOf(userId)) return noRecord();
    let raw = "";
    req.on("data", (chunk) => (raw += chunk));
    req.on("end", () => {
      const body = JSON.parse(raw || "{}");
      const tooLong = (body.blocker_items ?? []).some((item) => item.description.length > 500);
      if (tooLong) {
        send(422, {
          detail: [
            {
              type: "string_too_long",
              loc: ["body", "blocker_items", 0, "description"],
              msg: "String should have at most 500 characters",
            },
          ],
        });
        return;
      }
      send(200, confirmed(userId, body));
    });
    return true;
  }
  if (p === "/me/focus") return reply(send, 200, focusOf(userId));

  if (p === "/portfolio/heatmap") {
    if (!has(roles, "mgr", "exec", "admin")) return refuse();
    const root = url.searchParams.get("program_root_id") ?? consoleData.programs[0].id;
    return reply(send, 200, heatmap(root));
  }
  if (p === "/portfolio/attention") {
    if (!has(roles, "mgr", "exec", "admin")) return refuse();
    return reply(send, 200, attentionFor(url.searchParams.get("program_root_id")));
  }
  if ((m = p.match(/^\/graph\/programs\/([^/]+)\/tree$/))) {
    if (!has(roles, "sm", "po", "mgr", "admin")) return refuse();
    return reply(send, 200, teamGraph(m[1]));
  }
  if ((m = p.match(/^\/pods\/([^/]+)\/checkins$/))) {
    if (!has(roles, "sm", "mgr", "admin")) return refuse();
    return reply(send, 200, podCheckins(m[1]));
  }
  if (p === "/persona/briefs") {
    if (!has(roles, "sm", "po", "mgr", "exec", "admin")) return refuse();
    const kind = url.searchParams.get("kind");
    const limit = Number(url.searchParams.get("limit") ?? 20);
    const briefs = manyBriefs.filter((b) => !kind || b.kind === kind).slice(0, limit);
    return reply(send, 200, { briefs });
  }
  if ((m = p.match(/^\/cross-person-requests\/([^/]+)\/status$/)) && post) {
    const found = consoleData.requests.find((r) => r.id === m[1]);
    if (!found) return reply(send, 404, { detail: `cross-person request ${m[1]} not found` });
    let raw = "";
    req.on("data", (chunk) => (raw += chunk));
    req.on("end", () => {
      found.status = JSON.parse(raw || "{}").status ?? found.status;
      found.updated_at = new Date().toISOString();
      send(200, found);
    });
    return true;
  }
  return false;
}
