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
//   - Kai's tasks, each with the tracker's status, a due date, his own ETA and what he
//     last said, and a per-task Update (POST /me/tasks/{id}/update) that holds the
//     backend's rules and shows on his scrum master's pod task table. Write-back is
//     "ask" (MOCK_WRITE_BACK=auto or off to change it);
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

// What the backend writes for a day whose replies said nothing about the work
// (core/application/status_summaries.py NON_STATUS_REPLY_SUMMARY).
const NON_STATUS_REPLY = "Replied without a status update. Current status is unknown.";

/**
 * Kai has not answered since 5 Oct: the status on the card is that day's, carried
 * forward. Zoe replied in chat and has not confirmed it in the console (her scrum
 * master's board counts her as replied). Sofia replied, but said nothing about her
 * work, so her status is unknown.
 */
function initialStatus(userId) {
  if (userId === "U1005") {
    return {
      ...consoleData.myStatus(userId),
      developer_confirmed: false,
      confirmed_at: null,
      summary: "Cart promo stacking merged; address autocomplete is in review.",
    };
  }
  if (userId === "U1009") {
    return {
      source: "unknown",
      developer_confirmed: false,
      summary: NON_STATUS_REPLY,
      blockers: [],
      blocker_details: [],
      eta_change_days: null,
      status_as_of: TODAY,
      confirmed_at: null,
    };
  }
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

// ---- tasks, one at a time ---------------------------------------------------------

const WRITE_BACK = ["auto", "ask", "off"].includes(process.env.MOCK_WRITE_BACK)
  ? process.env.MOCK_WRITE_BACK
  : "ask";
const STATES = ["todo", "in_progress", "in_review", "blocked", "done"];
const STATE_WORDS = {
  todo: "to do",
  in_progress: "in progress",
  in_review: "in review",
  blocked: "blocked",
  done: "done",
};
const TRACKER_NAMES = {
  todo: "To Do",
  in_progress: "In Progress",
  in_review: "In Review",
  blocked: "Blocked",
  done: "Done",
};
// As the backend's IssueState reads a tracker status: a review status is "in progress".
const trackerAs = (status) =>
  /done|closed|resolved/i.test(status ?? "")
    ? "done"
    : /to ?do|backlog|open/i.test(status ?? "")
      ? "todo"
      : /block/i.test(status ?? "")
        ? "blocked"
        : "in_progress";
// The backend's day label (day_label): "Oct 9".
const dayLabel = (iso) =>
  new Date(`${iso}T12:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric" });
const daysBetween = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / 864e5);
const stamp = () => `${TODAY}T${new Date().toISOString().slice(11)}`;
const said = (state, note, at, via) => ({ state, note, at, via });

const task = (id, name, tracker_status, deadline, extra = {}) => ({
  id,
  name,
  rag: "unknown",
  source: "unknown",
  confidence: null,
  deadline,
  tracker_status,
  my_eta: null,
  my_eta_label: null,
  last_update: null,
  blocker_ids: [],
  can_move_in_tracker: true,
  ...extra,
});

/**
 * Kai's tasks, one of each kind the list sorts: blocked (the sandbox blocker), past due,
 * an ETA after the due date, in progress, to do, and two done. Anyone else keeps the
 * generic two, with the fields the backend now sends.
 */
function initialTasks(userId) {
  if (userId === "U1007") {
    return [
      task("CHK-103", "3-D Secure step-up", "In Progress", "2026-10-23", {
        blocker_ids: ["b-sandbox"],
        last_update: said("in_progress", null, "2026-10-05T08:40:00Z", "chat"),
        merge_request: "payments-api !12",
      }),
      task("CHK-106", "Card vault token rotation", "In Progress", "2026-10-02", {
        my_eta: "2026-10-08",
        my_eta_label: "Thursday",
        last_update: said("in_progress", null, "2026-10-02T09:05:00Z", "chat"),
      }),
      task("CHK-107", "Checkout error copy", "In Review", "2026-10-07", {
        my_eta: "2026-10-09",
        my_eta_label: "Oct 9",
        last_update: said("in_review", "MR !41 waits on Liam", "2026-10-05T15:10:00Z", "console"),
      }),
      task("CHK-108", "Apple Pay domain check", "In Progress", "2026-10-16", {
        my_eta: "2026-10-14",
        my_eta_label: "Oct 14",
      }),
      task("CHK-109", "3-D Secure analytics events", "To Do", "2026-10-30"),
      task("CHK-099", "Saved cards list", "Done", "2026-09-25"),
      task("CHK-100", "Payment method picker", "Done", "2026-09-30"),
    ];
  }
  const status = statusOf(userId);
  return consoleData.focus(userId).tasks.map((t) => ({
    ...task(t.id, t.name, null, t.deadline),
    ...t,
    blocker_ids: status.blocker_details
      .filter((b) => b.work_item_id === t.id)
      .map((b) => b.blocker_id),
    can_move_in_tracker: false,
  }));
}
const taskLists = new Map();
const tasksOf = (userId) => {
  if (!taskLists.has(userId)) taskLists.set(userId, initialTasks(userId));
  return taskLists.get(userId);
};
// Without the mock's own `merge_request`, the shape /me/focus sends.
const taskDto = ({ merge_request: _mr, ...rest }) => rest;

// Each person's task updates of the day, in order: today's partial summary is built from them.
const dayUpdates = new Map();
const UPDATE_LEAD = "Updated tasks in OpenProgram:";

function daySummary(userId) {
  const byTask = new Map();
  for (const u of dayUpdates.get(userId) ?? []) {
    const now = byTask.get(u.id) ?? { label: u.id, words: [] };
    if (u.state) now.state = u.state;
    if (u.eta !== undefined) now.eta = u.eta;
    if (u.added) now.added = true;
    if (u.resolved) now.resolved = true;
    byTask.set(u.id, now);
  }
  const parts = [...byTask.values()].map((t) =>
    [
      `${t.label}${t.state ? ` ${STATE_WORDS[t.state]}` : ""}`,
      t.eta ? `ETA ${dayLabel(t.eta)}` : null,
      t.added ? "blocker added" : null,
      t.resolved ? "blocker resolved" : null,
    ]
      .filter(Boolean)
      .join(", "),
  );
  return `${UPDATE_LEAD} ${parts.join("; ")}.`;
}

// The largest ETA change of the day: the biggest slip, else the biggest pull-in (largest_slip).
const largestSlip = (changes) =>
  changes
    .filter(Boolean)
    .reduce(
      (best, days) =>
        best === null ||
        (days > 0 && best < 0) ||
        (days > 0 === best > 0 && Math.abs(days) > Math.abs(best))
          ? days
          : best,
      null,
    );

const fieldError = (send, loc, msg, type = "value_error") =>
  send(422, { detail: [{ type, loc: ["body", loc], msg }] });

/** POST /me/tasks/{id}/update: the rules of backend task_update_service, checked before writing. */
function updateTask(userId, taskId, body, send) {
  const t = tasksOf(userId).find((item) => item.id === taskId);
  if (!t) return send(404, { detail: "task is not assigned to you" });
  const known = ["state", "eta", "note", "add_blocker", "resolve_blocker_ids", "move_in_tracker"];
  const unknown = Object.keys(body).find((key) => !known.includes(key));
  if (unknown)
    return fieldError(send, unknown, "Extra inputs are not permitted", "extra_forbidden");
  if (body.state != null && !STATES.includes(body.state)) {
    return fieldError(
      send,
      "state",
      "Input should be 'todo', 'in_progress', 'in_review', 'blocked' or 'done'",
    );
  }
  for (const field of ["note", "add_blocker"]) {
    if ((body[field] ?? "").length > 500) {
      return fieldError(
        send,
        field,
        "String should have at most 500 characters",
        "string_too_long",
      );
    }
  }
  const note = (body.note ?? "").trim() || null;
  const added = (body.add_blocker ?? "").trim() || null;
  const resolving = body.resolve_blocker_ids ?? [];
  const etaSent = "eta" in body;
  if (!body.state && !etaSent && !note && !added && resolving.length === 0) {
    return send(422, {
      detail:
        "Nothing to update: send a state, an ETA, a note, a blocker to add or one to resolve.",
    });
  }
  if (etaSent && body.eta && body.eta < TODAY) {
    return send(422, { detail: "The ETA can't be before today." });
  }
  const notOpen = resolving.filter((id) => !t.blocker_ids.includes(id));
  if (notOpen.length > 0) {
    return send(422, {
      detail: `Not an open blocker of this task: ${notOpen.join(", ")}. Reload the task and try again.`,
    });
  }
  const left = t.blocker_ids.filter((id) => !resolving.includes(id));
  if (body.state === "blocked" && !added && left.length === 0) {
    return send(422, {
      detail: "Blocked needs a blocker: add one, or keep one of the task's open blockers.",
    });
  }

  // Blockers: the task's own change; every other blocker of the person stays as it was.
  const status = statusOf(userId);
  let details = status.blocker_details.filter((b) => !resolving.includes(b.blocker_id));
  if (added) {
    const blocker = {
      blocker_id: `b-${Date.now()}`,
      description: added,
      work_item_id: t.id,
      work_item_name: t.name,
      pod_id: "pod-payments",
      unattributed: false,
      first_seen_on: TODAY,
      age_days: 0,
    };
    details = [...details, blocker];
    left.push(blocker.blocker_id);
  }
  t.blocker_ids = left;

  // The ETA, and how far it moved against the one the task had.
  let slip = null;
  if (etaSent) {
    if (body.eta && t.my_eta) slip = daysBetween(t.my_eta, body.eta);
    t.my_eta = body.eta ?? null;
    t.my_eta_label = body.eta ? dayLabel(body.eta) : null;
  }
  // A statement is a state or a note; an ETA or a blocker alone is not one.
  if (body.state || note) t.last_update = said(body.state ?? null, note, stamp(), "console");

  dayUpdates.set(userId, [
    ...(dayUpdates.get(userId) ?? []),
    {
      id: t.id,
      state: body.state ?? null,
      eta: etaSent ? body.eta : undefined,
      added: Boolean(added),
      resolved: resolving.length > 0,
    },
  ]);
  const replied =
    status.status_as_of === TODAY &&
    ["confirmed", "partial"].includes(status.source) &&
    !status.summary.startsWith(UPDATE_LEAD);
  const slips = [slip, status.status_as_of === TODAY ? status.eta_change_days : null].filter(
    Boolean,
  );
  const next = {
    ...status,
    ...(replied
      ? {}
      : {
          source: "partial",
          developer_confirmed: false,
          summary: daySummary(userId),
          confirmed_at: new Date().toISOString(),
        }),
    status_as_of: TODAY,
    blocker_details: details,
    blockers: details.map((b) => b.description),
    eta_change_days: largestSlip(slips),
  };
  statuses.set(userId, next);

  // Jira, behind the gates: write-back on, the assignee, a state sent, the tick.
  let tracker = null;
  if (body.move_in_tracker && body.state) {
    const target = TRACKER_NAMES[body.state];
    if (WRITE_BACK === "off" || !t.can_move_in_tracker) {
      tracker = {
        outcome: "off",
        detail: "Not moved in Jira: write-back is off.",
        merge_requests: [],
      };
    } else if (body.state === "done" && t.merge_request) {
      tracker = {
        outcome: "held_open_mr",
        detail: `Not moved in Jira: ${t.merge_request} is still open.`,
        merge_requests: [t.merge_request],
      };
    } else if (
      trackerAs(t.tracker_status) === (body.state === "in_review" ? "in_progress" : body.state)
    ) {
      tracker = {
        outcome: "no_change",
        detail: `Already ${t.tracker_status} in Jira.`,
        merge_requests: [],
      };
    } else {
      t.tracker_status = target;
      tracker = { outcome: "applied", detail: `Moved to ${target} in Jira.`, merge_requests: [] };
    }
  }
  return send(200, { task: taskDto(t), status: next, tracker });
}

/**
 * The pod's task table with what each owner last said and their ETA. Kai's tasks join
 * the generic list, with his blockers as they stand.
 */
function podTasksOf(podId) {
  const base = consoleData.podTasks(podId);
  if (podId !== "pod-payments") return base;
  const kai = tasksOf("U1007");
  const details = statusOf("U1007").blocker_details;
  const rows = base.tasks.filter((row) => !kai.some((t) => t.id === row.id));
  const kaiRows = kai.map((t) => {
    const open_blockers = details
      .filter((b) => t.blocker_ids.includes(b.blocker_id))
      .map(({ blocker_id, description, first_seen_on, age_days }) => ({
        blocker_id,
        description,
        first_seen_on,
        age_days,
      }));
    return {
      id: t.id,
      name: t.name,
      rag: t.tracker_status === "Done" ? "green" : "unknown",
      source: "inferred",
      confidence: null,
      deadline: t.deadline,
      owners: [{ id: "U1007", name: "Kai Thompson" }],
      blocked: open_blockers.length > 0,
      open_blockers,
      tracker_status: t.tracker_status,
      last_update: t.last_update,
      last_update_by: t.last_update ? "Kai Thompson" : null,
      eta: t.my_eta,
      eta_label: t.my_eta_label,
    };
  });
  const all = [...kaiRows, ...rows].sort((a, b) => Number(b.blocked) - Number(a.blocked));
  return { ...base, tasks: all };
}

function focusOf(userId) {
  const status = statusOf(userId);
  const base = consoleData.focus(userId);
  return {
    ...base,
    as_of: TODAY,
    write_back: WRITE_BACK,
    tasks: tasksOf(userId).map(taskDto),
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
      // Confirmed in the console, or partly replied through task updates.
      const state = kai.source === "partial" ? "partial" : "confirmed";
      return { ...d, state, source: kai.source, status_as_of: TODAY, summary: kai.summary };
    }
    if (d.developer_id === "U1009") {
      // Replied without a status: the backend closes the day as unknown, with this summary.
      return {
        ...d,
        state: "missing",
        source: "unknown",
        status_as_of: TODAY,
        summary: NON_STATUS_REPLY,
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
  // The own-status routes answer 404 both for no member record and for no status yet, and
  // say which in the detail (backend STATUS_NOT_A_MEMBER_DETAIL, STATUS_NONE_YET_DETAIL).
  const noRecord = () =>
    reply(send, 404, { detail: "status is not available: no member record for this person" });
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
  if ((m = p.match(/^\/me\/tasks\/([^/]+)\/update$/)) && post) {
    if (!nameOf(userId)) return reply(send, 404, { detail: "task is not assigned to you" });
    const taskId = decodeURIComponent(m[1]);
    let raw = "";
    req.on("data", (chunk) => (raw += chunk));
    req.on("end", () => updateTask(userId, taskId, JSON.parse(raw || "{}"), send));
    return true;
  }
  if ((m = p.match(/^\/pods\/([^/]+)\/tasks$/))) {
    // The generic mock's rule: a pod's detail is for its scrum master, a manager or an admin.
    if (!has(roles, "sm", "mgr", "admin")) return refuse();
    return reply(send, 200, podTasksOf(m[1]));
  }

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
