// Mock handlers for the release readiness lane: Overall's Release readiness section
// (the board, Run check now, link, not applicable, dismiss, edit, create, reopen,
// history) and Admin › Release readiness (the agent's settings, criteria, examples,
// Try on a project). NOT real data: shapes follow src/api/generated.ts, names and
// keys follow the demo roster. Only Checkout Revamp has findings; its Release 1.0
// is the mock's one release. Wired from ../mock-api.mjs before the admin-config mock.
//
// Roles as the backend has them: a developer reads none of it; an executive reads
// the project's and release's rows without drafts or buttons; Ira (scrum master)
// reads and acts on the project's rows and her Payments Pod's; a product owner
// acts on everything but waiving a blocking criterion, which a manager or an admin does.
// A scrum master's read of a project none of their pods works on is refused, as the
// backend refuses it; the console never asks.
import * as consoleData from "../mock-console.mjs";

const PROJECT = "project-checkout";
const READ_OUTSIDE = "You read the projects your own pods work on, and this is not one of them.";
const NOT_YOURS = "You can act on readiness only for pods you run and the projects they work on.";
const RELEASE = { release_id: "rel-1-0", name: "Release 1.0" };
const NAMES = {
  U1001: "Asha Rao",
  U1003: "Mina Patel",
  U1006: "Ira Novak",
  U1007: "Kai Thompson",
  U1011: "Elena Fischer",
};
const IRA_PODS = ["pod-payments"];
const AT = "2026-10-06T07:45:00Z";

const settings = {
  enabled: true,
  auto_suggest: true,
  create_in_jira: false,
  issue_type: "Task",
  labels: ["release-readiness"],
  updated_at: "2026-10-01T09:00:00Z",
  updated_by: "U1001",
};
const WRITEBACK = true;

const matcher = (kind, value, strength = "evidence") => ({ kind, value, strength });
const criterion = (id, name, evidence, applies_to, matchers, extra = {}) => ({
  criterion_id: id,
  version: 1,
  name,
  evidence,
  applies_to,
  required_before: "production",
  lead_working_days: 10,
  severity: "blocking",
  needs_done: true,
  when_labels: [],
  when_types: [],
  matchers,
  draft: {
    project_key: "",
    issue_type: "",
    summary: "{criterion} for {scope}",
    description: "",
    labels: [id],
  },
  enabled: true,
  ...extra,
});

const EXAMPLES = [
  criterion(
    "security-review",
    "Security review",
    "A security review of the release's changes, with its findings closed or accepted.",
    "release",
    [matcher("label", "security-review"), matcher("title_phrase", "security review")],
  ),
  criterion(
    "load-test",
    "Load test",
    "A load test at the expected peak, with its results accepted.",
    "release",
    [
      matcher("label", "load-test"),
      matcher("title_phrase", "load test"),
      matcher("title_words", "performance test", "candidate"),
    ],
  ),
  criterion(
    "runbook-handover",
    "Runbook and handover",
    "A runbook for operating the solution, handed over to the team that runs it.",
    "project",
    [matcher("title_phrase", "runbook"), matcher("title_phrase", "handover")],
  ),
  criterion(
    "data-protection-impact-assessment",
    "Data-protection impact assessment",
    "An assessment of how the solution handles personal data, signed off.",
    "project",
    [matcher("title_phrase", "DPIA"), matcher("title_phrase", "data protection impact")],
    { when_labels: ["personal-data"] },
  ),
  criterion(
    "accessibility-check",
    "Accessibility check",
    "An accessibility review of the release's user-facing changes.",
    "release",
    [
      matcher("label", "accessibility"),
      matcher("title_phrase", "accessibility review"),
      matcher("title_words", "accessibility", "candidate"),
    ],
    { severity: "advisory" },
  ),
  criterion(
    "change-approval",
    "Change approval",
    "An approved change request for the production deployment.",
    "release",
    [matcher("label", "change-approval"), matcher("title_phrase", "change request")],
  ),
];

const ON_CALL = criterion(
  "on-call-handover",
  "On-call handover",
  "The pod's on-call rota and alerts handed over before production.",
  "pod",
  [matcher("title_phrase", "on-call")],
);

/** The tenant's criteria: four examples added, and one of its own. */
const criteria = [
  ...EXAMPLES.filter((item) =>
    [
      "security-review",
      "load-test",
      "runbook-handover",
      "accessibility-check",
      "change-approval",
    ].includes(item.criterion_id),
  ).map((item) => structuredClone(item)),
  ON_CALL,
];

const urgency = (kind, due_on = null, days = null, delivery = null, stage_key = null) => ({
  kind,
  due_on,
  working_days_left: days,
  delivery_date: delivery,
  stage_key,
});
const draftOf = (summary, labels, description) => ({
  project_key: "CHK",
  issue_type: "Task",
  summary,
  description,
  labels: [...labels, "release-readiness"],
});

let findings = [
  {
    finding_id: "rf_security",
    criterion_id: "security-review",
    scope: { kind: "release", id: RELEASE.release_id, name: RELEASE.name },
    state: "missing",
    done: false,
    decided_by: "rules",
    held: false,
    evidence: [],
    candidates: [],
    reason: 'No issue in Release 1.0 is labelled security-review or says "security review".',
    urgency: urgency("due_soon", "2026-10-16", 8, "2026-10-30"),
    person_decision: null,
    suggestion: {
      suggestion_id: "rs_security",
      status: "open",
      version: 1,
      draft: draftOf(
        "Security review for Release 1.0 of Checkout Revamp",
        ["security-review"],
        "Release 1.0 of Checkout Revamp needs a security review before production. No Jira issue in Release 1.0 tracks one yet.\n\nWhat counts as done: A security review of the release's changes, with its findings closed or accepted.\n\nNeeded by 16 Oct, 10 working days before the delivery date of 30 Oct.",
      ),
      marker_label: "op-rr-3fa2c1d0",
      created_issue_key: null,
      created_by_name: null,
      created_at: null,
      dismissed: null,
    },
  },
  {
    finding_id: "rf_load",
    criterion_id: "load-test",
    scope: { kind: "release", id: RELEASE.release_id, name: RELEASE.name },
    state: "unsure",
    done: false,
    decided_by: "rules",
    held: false,
    evidence: [],
    candidates: [
      { issue_key: "CHK-110", title: "Storefront performance test budget", why: "title_words" },
    ],
    reason: "Only the title's words match: CHK-110.",
    urgency: urgency("due_soon", "2026-10-16", 8, "2026-10-30"),
    person_decision: null,
    suggestion: null,
  },
  {
    finding_id: "rf_change",
    criterion_id: "change-approval",
    scope: { kind: "release", id: RELEASE.release_id, name: RELEASE.name },
    state: "covered",
    done: true,
    decided_by: "rules",
    held: false,
    evidence: [
      {
        issue_key: "CHK-120",
        title: "Change request for the 1.0 cutover",
        status: "Done",
        done: true,
        how: 'says "change request"',
        url: "",
        note: "",
      },
    ],
    candidates: [],
    reason: "",
    urgency: urgency("later", "2026-10-16", 8, "2026-10-30"),
    person_decision: null,
    suggestion: null,
  },
  {
    finding_id: "rf_access",
    criterion_id: "accessibility-check",
    scope: { kind: "release", id: RELEASE.release_id, name: RELEASE.name },
    state: "unsure",
    done: false,
    decided_by: "rules",
    held: false,
    evidence: [],
    candidates: [
      { issue_key: "CHK-204", title: "Mini-cart accessibility audit", why: "title_words" },
    ],
    reason: "Only the title's words match: CHK-204.",
    urgency: urgency("later", "2026-10-16", 8, "2026-10-30"),
    person_decision: null,
    suggestion: null,
  },
  {
    finding_id: "rf_runbook",
    criterion_id: "runbook-handover",
    scope: { kind: "project", id: PROJECT, name: "Checkout Revamp" },
    state: "covered",
    done: false,
    decided_by: "rules",
    held: false,
    evidence: [
      {
        issue_key: "CHK-104",
        title: "Capture retry runbook",
        status: "In Progress",
        done: false,
        how: 'says "runbook"',
        url: "",
        note: "",
      },
    ],
    candidates: [],
    reason: "",
    urgency: urgency("due_soon", "2026-10-16", 8, "2026-10-30"),
    person_decision: null,
    suggestion: null,
  },
  {
    finding_id: "rf_oncall",
    criterion_id: "on-call-handover",
    scope: { kind: "pod", id: "pod-payments", name: "Payments Pod" },
    state: "missing",
    done: false,
    decided_by: "rules",
    held: false,
    evidence: [],
    candidates: [],
    reason: 'No issue in Payments Pod says "on-call".',
    urgency: urgency("no_date"),
    person_decision: null,
    suggestion: {
      suggestion_id: "rs_oncall",
      status: "open",
      version: 1,
      draft: draftOf(
        "On-call handover for Payments Pod",
        ["on-call-handover"],
        "Payments Pod needs an on-call handover before production. No Jira issue in Payments Pod tracks one yet.\n\nWhat counts as done: The pod's on-call rota and alerts handed over before production.",
      ),
      marker_label: "op-rr-91be0a77",
      created_issue_key: null,
      created_by_name: null,
      created_at: null,
      dismissed: null,
    },
  },
];
const history = {};
let nextKey = 301;

function audit(findingId, actor, action, reason = null) {
  (history[findingId] ??= []).push({
    at: new Date().toISOString(),
    actor,
    actor_name: NAMES[actor] ?? null,
    action,
    reason,
  });
}
for (const finding of findings) {
  audit(finding.finding_id, "agent", "state_changed");
  if (finding.suggestion) audit(finding.finding_id, "agent", "drafted");
}

const has = (roles, ...wanted) => roles.some((role) => wanted.includes(role));
const isBroad = (roles) => has(roles, "admin", "mgr", "po");
const mayAct = (roles) => has(roles, "admin", "mgr", "po", "sm");

/** Whether one of the person's pods (the roster's) works on the project. */
function runsPodOf(userId, projectId) {
  const pods = consoleData.roster.find((person) => person.id === userId)?.pods ?? [];
  const project = consoleData.projects.find((item) => item.id === projectId);
  return Boolean(project?.pod_ids.some((id) => pods.includes(id)));
}

/** Whose rows: every pod's for a broad reader, Ira's pod for a scrum master, none for an executive. */
function sees(roles, finding) {
  if (finding.scope.kind !== "pod") return true;
  if (isBroad(roles)) return true;
  if (has(roles, "sm")) return IRA_PODS.includes(finding.scope.id);
  return false;
}

function shown(finding) {
  return finding.person_decision?.kind === "not_applicable" ? "not_applicable" : finding.state;
}

function canOf(roles, finding) {
  const crit = criteria.find((item) => item.criterion_id === finding.criterion_id);
  const act = mayAct(roles) && sees(roles, finding);
  const none = {
    create: false,
    create_off_reason: null,
    link: false,
    not_applicable: false,
    dismiss: false,
    edit: false,
    reopen: false,
    draft: false,
  };
  if (!act) return none;
  const undecided = !finding.person_decision;
  const missing = undecided && finding.state === "missing";
  const open = finding.suggestion?.status === "open";
  const off = !(missing && open)
    ? null
    : !settings.create_in_jira
      ? "Creating issues from OpenProgram is off for this tenant."
      : WRITEBACK
        ? null
        : "Creating issues from OpenProgram needs Jira write-back on.";
  return {
    create: missing && open && off === null,
    create_off_reason: off,
    link: undecided,
    not_applicable: undecided && (crit?.severity !== "blocking" || has(roles, "mgr", "admin")),
    dismiss: missing && open,
    edit: missing && open,
    reopen: !undecided || finding.suggestion?.status === "dismissed",
    draft: missing && !finding.suggestion && !settings.auto_suggest,
  };
}

function response(roles, finding) {
  const crit = criteria.find((item) => item.criterion_id === finding.criterion_id);
  const exec = !mayAct(roles);
  return {
    finding_id: finding.finding_id,
    criterion: {
      criterion_id: crit.criterion_id,
      name: crit.name,
      severity: crit.severity,
      required_before: crit.required_before,
      evidence: crit.evidence,
      needs_done: crit.needs_done,
    },
    scope: finding.scope,
    state: shown(finding),
    done: finding.done,
    decided_by: finding.decided_by,
    held: finding.held,
    evidence: finding.evidence,
    candidates: finding.candidates,
    reason: finding.reason,
    urgency: finding.urgency,
    person_decision: finding.person_decision,
    suggestion: exec ? null : finding.suggestion,
    can: canOf(roles, finding),
    checked_at: AT,
  };
}

const ORDER = { missing: 0, unsure: 1, covered: 2, not_applicable: 4 };
const KIND = { release: 0, project: 1, pod: 2 };

function board(roles, releaseId) {
  const live = findings.filter((item) =>
    criteria.some((crit) => crit.criterion_id === item.criterion_id && crit.enabled),
  );
  const rows = live
    .filter((item) => sees(roles, item))
    .filter((item) =>
      releaseId
        ? item.scope.kind === "release" || item.scope.kind === "project"
        : item.scope.kind !== "release",
    )
    .map((item) => response(roles, item))
    .sort(
      (a, b) =>
        KIND[a.scope.kind] - KIND[b.scope.kind] ||
        Number(a.criterion.severity !== "blocking") - Number(b.criterion.severity !== "blocking") ||
        ORDER[a.state] +
          (a.state === "covered" && a.done ? 1 : 0) -
          (ORDER[b.state] + (b.state === "covered" && b.done ? 1 : 0)),
    );
  const releaseRows = live.filter((item) => item.scope.kind === "release");
  // The day report's lines: the release's gaps too in the whole project, no pod's in a release.
  const urgent = live
    .filter((item) => sees(roles, item))
    .filter((item) => !releaseId || item.scope.kind !== "pod")
    .filter((item) => {
      const crit = criteria.find((c) => c.criterion_id === item.criterion_id);
      return (
        crit.severity === "blocking" &&
        !item.person_decision &&
        !item.held &&
        (item.state === "missing" || item.state === "unsure") &&
        ["due_soon", "overdue", "stage_reached"].includes(item.urgency.kind)
      );
    })
    .map((item) => {
      const crit = criteria.find((c) => c.criterion_id === item.criterion_id);
      const what = `a ${crit.name.charAt(0).toLowerCase()}${crit.name.slice(1)}`;
      const tail =
        item.state === "missing"
          ? "and none is in Jira"
          : `and Jira has only a possible match: ${item.candidates.map((c) => c.issue_key).join(", ")}`;
      return `${item.scope.name} needs ${what} within ${item.urgency.working_days_left} working days, ${tail}.`;
    });
  const all = rows;
  return {
    project_id: PROJECT,
    pod_id: null,
    release_id: releaseId || null,
    scope_name: releaseId ? RELEASE.name : "Checkout Revamp",
    agent: {
      enabled: settings.enabled,
      create_in_jira: settings.create_in_jira,
      writeback_enabled: WRITEBACK,
      last_run_at: AT,
      last_run_status: "ok",
      data_as_of: "2026-10-06T07:00:00Z",
      stale: false,
    },
    summary: {
      blocking_missing: all.filter(
        (r) => r.state === "missing" && r.criterion.severity === "blocking",
      ).length,
      missing: all.filter((r) => r.state === "missing").length,
      unsure: all.filter((r) => r.state === "unsure").length,
      covered: all.filter((r) => r.state === "covered").length,
      not_applicable: all.filter((r) => r.state === "not_applicable").length,
      total: all.length,
    },
    findings: rows,
    releases: releaseId
      ? []
      : [
          {
            ...RELEASE,
            missing: releaseRows.filter((r) => shown(r) === "missing").length,
            unsure: releaseRows.filter((r) => shown(r) === "unsure").length,
            total: releaseRows.length,
          },
        ],
    important: urgent.slice(0, 3),
    can_run: mayAct(roles) && settings.enabled,
  };
}

function readJson(req) {
  return new Promise((resolve) => {
    let raw = "";
    req.on("data", (chunk) => (raw += chunk));
    req.on("end", () => {
      try {
        resolve(JSON.parse(raw || "{}"));
      } catch {
        resolve(undefined);
      }
    });
  });
}

const reasonOk = (text) => {
  const clean = String(text ?? "")
    .trim()
    .replace(/\s+/g, " ");
  return clean.length >= 3 && clean.length <= 300 ? clean : null;
};

/** Answers this lane's endpoints and returns true, or returns false for the next handler. */
export function api(req, url, roles, actingAs, send, deny) {
  const p = url.pathname;
  const method = req.method ?? "GET";
  const userId = actingAs ?? "U1001";
  const done = (status, body) => {
    send(status, body);
    return true;
  };
  const write = (handle) => {
    readJson(req).then((body) =>
      body === undefined ? send(422, { detail: "The request body is not JSON." }) : handle(body),
    );
    return true;
  };
  let m;

  // ---- the board ----
  if ((m = /^\/projects\/([^/]+)\/readiness(\/run)?$/.exec(p))) {
    const [, projectId, run] = m;
    if (!has(roles, "admin", "mgr", "po", "sm", "exec")) {
      deny();
      return true;
    }
    if (!isBroad(roles) && !has(roles, "exec") && !runsPodOf(userId, projectId)) {
      return done(403, { detail: run ? NOT_YOURS : READ_OUTSIDE });
    }
    const releaseId = url.searchParams.get("release_id") ?? "";
    if (projectId !== PROJECT) {
      const empty = {
        ...board(roles, ""),
        project_id: projectId,
        findings: [],
        releases: [],
        important: [],
      };
      empty.scope_name = "This project";
      return done(
        200,
        run
          ? {
              run: { status: "ok", scopes: 1, changed: 0, missing: 0, unsure: 0, covered: 0 },
              board: empty,
            }
          : empty,
      );
    }
    if (!run) return done(200, board(roles, releaseId));
    if (!mayAct(roles)) {
      deny();
      return true;
    }
    if (!settings.enabled)
      return done(409, { detail: "The readiness agent is off for this tenant." });
    const current = board(roles, releaseId);
    return done(200, {
      run: {
        status: "ok",
        scopes: 3,
        changed: 0,
        missing: current.summary.missing,
        unsure: current.summary.unsure,
        covered: current.summary.covered,
      },
      board: current,
    });
  }

  // ---- a person's actions ----
  if ((m = /^\/readiness-findings\/([^/]+)\/(link|not-applicable|reopen|draft|history)$/.exec(p))) {
    const [, id, action] = m;
    const finding = findings.find((item) => item.finding_id === id);
    if (!finding) return done(404, { detail: `No finding '${id}'.` });
    if (action === "history") {
      return done(200, { finding_id: id, entries: history[id] ?? [] });
    }
    const can = canOf(roles, finding);
    if (!mayAct(roles) || !sees(roles, finding)) {
      return done(403, {
        detail: NOT_YOURS,
      });
    }
    if (action === "reopen") {
      if (!can.reopen) return done(409, { detail: "Nothing here was decided by a person." });
      if (finding.person_decision?.kind === "linked" && !finding.person_decision.created) {
        finding.evidence = [];
        finding.state = finding.candidates.length ? "unsure" : "missing";
        finding.done = false;
      }
      finding.person_decision = null;
      finding.decided_by = "rules";
      if (finding.suggestion?.status === "dismissed") {
        finding.suggestion = {
          ...finding.suggestion,
          status: "open",
          dismissed: null,
          version: finding.suggestion.version + 1,
        };
      }
      audit(id, userId, "reopened");
      return done(200, response(roles, finding));
    }
    if (action === "draft") return done(200, response(roles, finding));
    return write((body) => {
      if (action === "not-applicable") {
        const crit = criteria.find((item) => item.criterion_id === finding.criterion_id);
        if (crit.severity === "blocking" && !has(roles, "mgr", "admin")) {
          return send(403, {
            detail: "Only a manager or an admin marks a blocking criterion not applicable.",
          });
        }
        const reason = reasonOk(body.reason);
        if (!reason) return send(422, { detail: "Give a reason of 3 to 300 characters." });
        finding.person_decision = {
          kind: "not_applicable",
          by: userId,
          by_name: NAMES[userId] ?? null,
          at: new Date().toISOString(),
          reason,
          issue_key: "",
          url: "",
          note: "",
          created: false,
        };
        finding.decided_by = "person";
        audit(id, userId, "not_applicable", reason);
        return send(200, response(roles, finding));
      }
      const key = String(body.issue_key ?? "")
        .trim()
        .toUpperCase();
      const link = String(body.evidence_url ?? "").trim();
      if (Boolean(key) === Boolean(link)) {
        return send(422, { detail: "Link either a Jira issue or a record outside Jira." });
      }
      if (link && !link.startsWith("https://")) {
        return send(422, { detail: "A record outside Jira is a link starting with https://." });
      }
      if (key && !/^CHK-\d+$/.test(key)) {
        return send(422, { detail: `${key} is not among Jira's synced issues.` });
      }
      const candidate = finding.candidates.find((item) => item.issue_key === key);
      finding.person_decision = {
        kind: "linked",
        by: userId,
        by_name: NAMES[userId] ?? null,
        at: new Date().toISOString(),
        reason: "",
        issue_key: key,
        url: link,
        note: String(body.note ?? ""),
        created: false,
      };
      finding.state = "covered";
      finding.done = Boolean(link);
      finding.decided_by = "person";
      finding.evidence = [
        {
          issue_key: key,
          title: candidate?.title ?? (key ? "Linked issue" : ""),
          status: key ? "To Do" : "",
          done: Boolean(link),
          how: "linked",
          url: link,
          note: String(body.note ?? ""),
        },
      ];
      audit(id, userId, "linked");
      return send(200, response(roles, finding));
    });
  }

  if ((m = /^\/readiness-suggestions\/([^/]+)(?:\/(dismiss|create))?$/.exec(p))) {
    const [, id, action] = m;
    const finding = findings.find((item) => item.suggestion?.suggestion_id === id);
    if (!finding) return done(404, { detail: `No draft '${id}'.` });
    if (!mayAct(roles) || !sees(roles, finding)) {
      return done(403, {
        detail: NOT_YOURS,
      });
    }
    const suggestion = finding.suggestion;
    return write((body) => {
      if (suggestion.status === "created" && action === "create") {
        return send(200, {
          issue_key: suggestion.created_issue_key,
          url: null,
          created: false,
          finding: response(roles, finding),
        });
      }
      if (suggestion.status === "dismissed") {
        return send(409, { detail: "This draft was dismissed; reopen it first." });
      }
      if (action === "dismiss") {
        const reason = reasonOk(body.reason);
        if (!reason) return send(422, { detail: "Give a reason of 3 to 300 characters." });
        finding.suggestion = {
          ...suggestion,
          status: "dismissed",
          dismissed: {
            by: userId,
            by_name: NAMES[userId] ?? null,
            at: new Date().toISOString(),
            reason,
          },
        };
        audit(finding.finding_id, userId, "dismissed", reason);
        return send(200, response(roles, finding));
      }
      if (body.version !== suggestion.version) {
        return send(409, { detail: "The draft changed since you opened it; check it again." });
      }
      if (!action) {
        const draft = body.draft ?? {};
        if (!String(draft.summary ?? "").trim()) {
          return send(422, { detail: "A summary is 1 to 255 characters." });
        }
        finding.suggestion = { ...suggestion, draft, version: suggestion.version + 1 };
        audit(finding.finding_id, userId, "draft_edited");
        return send(200, response(roles, finding));
      }
      if (!settings.create_in_jira) {
        return send(409, { detail: "Creating issues from OpenProgram is off for this tenant." });
      }
      const key = `CHK-${nextKey++}`;
      finding.suggestion = {
        ...suggestion,
        status: "created",
        created_issue_key: key,
        created_by_name: NAMES[userId] ?? null,
        created_at: new Date().toISOString(),
      };
      finding.person_decision = {
        kind: "linked",
        by: userId,
        by_name: NAMES[userId] ?? null,
        at: new Date().toISOString(),
        reason: "",
        issue_key: key,
        url: "",
        note: "",
        created: true,
      };
      finding.state = "covered";
      finding.decided_by = "person";
      finding.evidence = [
        {
          issue_key: key,
          title: suggestion.draft.summary,
          status: "To Do",
          done: false,
          how: "created",
          url: "",
          note: "",
        },
      ];
      audit(finding.finding_id, userId, "create_requested");
      audit(finding.finding_id, userId, "created");
      return send(201, {
        issue_key: key,
        url: null,
        created: true,
        finding: response(roles, finding),
      });
    });
  }

  // ---- configuration ----
  if (p.startsWith("/config/readiness")) {
    if (!roles.includes("admin")) {
      deny();
      return true;
    }
    if (p === "/config/readiness" && method === "GET") {
      const added = new Set(criteria.map((item) => item.criterion_id));
      return done(200, {
        settings,
        is_default: false,
        criteria,
        examples: EXAMPLES.filter((item) => !added.has(item.criterion_id)),
        writeback_enabled: WRITEBACK,
        waive_roles: ["mgr"],
      });
    }
    if (p === "/config/readiness/settings" && method === "PUT") {
      return write((body) => {
        Object.assign(settings, {
          enabled: Boolean(body.enabled),
          auto_suggest: Boolean(body.auto_suggest),
          create_in_jira: Boolean(body.create_in_jira),
          issue_type: String(body.issue_type ?? "Task"),
          labels: body.labels ?? [],
          updated_at: new Date().toISOString(),
          updated_by: userId,
        });
        send(200, settings);
      });
    }
    if (p === "/config/readiness/criteria" && method === "PUT") {
      return write((body) => {
        if (!String(body.name ?? "").trim() || !(body.matchers ?? []).length) {
          return send(422, { detail: "A criterion needs a name of 1 to 80 characters." });
        }
        const id =
          body.criterion_id ||
          String(body.name)
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, "-")
            .replace(/^-|-$/g, "");
        const index = criteria.findIndex((item) => item.criterion_id === id);
        const saved = {
          ...body,
          criterion_id: id,
          version: index >= 0 ? criteria[index].version + 1 : 1,
        };
        if (index >= 0) criteria[index] = saved;
        else criteria.push(saved);
        send(200, saved);
      });
    }
    if ((m = /^\/config\/readiness\/criteria\/([^/]+)$/.exec(p)) && method === "DELETE") {
      const index = criteria.findIndex((item) => item.criterion_id === m[1]);
      if (index < 0) return done(404, { detail: `No criterion '${m[1]}'.` });
      criteria.splice(index, 1);
      return done(204, null);
    }
    if (p === "/config/readiness/criteria/preview" && method === "POST") {
      return write((body) => {
        const crit = body.criterion ?? {};
        const words = (crit.matchers ?? []).map((item) => String(item.value).toLowerCase());
        const runbook = words.some((word) => word.includes("runbook"));
        send(200, {
          rows: [
            {
              scope: { kind: "project", id: PROJECT, name: "Checkout Revamp" },
              applies: true,
              state: runbook ? "covered" : "missing",
              evidence: runbook
                ? [
                    {
                      issue_key: "CHK-104",
                      title: "Capture retry runbook",
                      status: "In Progress",
                      done: false,
                      how: 'says "runbook"',
                      url: "",
                      note: "",
                    },
                  ]
                : [],
              candidates: [],
              reason: "",
              urgency: "due_soon",
              due_on: "2026-10-16",
            },
          ],
        });
      });
    }
    return done(404, { detail: "Not found." });
  }
  return false;
}
