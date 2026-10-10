// Mock handlers for the admin-config lane: check-in preferences and consent,
// data sources (with the chat directory's Sync now), delivery stages, gates,
// escalation, integrations and branding. NOT real data: shapes follow
// src/api/generated.ts and the rules follow the backend (named in each block),
// so every write round-trips. State lives in memory and resets when the mock
// restarts. Integration secrets are never kept here, only which ones are set.
import * as consoleData from "../mock-console.mjs";
import { memberList } from "./admin-structure.mjs";
import { demoLogo } from "./shell.mjs";

const TENANT = "demo";
const now = () => new Date().toISOString();
const clone = (value) => JSON.parse(JSON.stringify(value));
const key = (name) => name.trim().replace(/\s+/g, " ").toLowerCase();
const sentence = (problems) => {
  const text = problems.join("; ");
  return text[0].toUpperCase() + text.slice(1) + ".";
};

/** The JSON body of a write, or undefined when it is not JSON. */
function readJson(req) {
  return new Promise((resolve) => {
    let raw = "";
    req.on("data", (chunk) => (raw += chunk));
    req.on("end", () => {
      try {
        resolve(raw ? JSON.parse(raw) : {});
      } catch {
        resolve(undefined);
      }
    });
  });
}

/** FastAPI's shape for a field it refused. */
const refused = (field, message) => ({
  detail: [{ type: "value_error", loc: ["body", field], msg: `Value error, ${message}` }],
});

// ---- Members ---------------------------------------------------------------------
// The members are the admin-structure mock's, which owns GET /config/members, so a
// person imported from the directory there is a member here too (check-ins, the
// matrix's member rules, who saved a connection). The executive starts outside them:
// she is the directory person that mock leaves for an import.

const members = () => memberList();

// ---- Check-in preferences and write-back consent ---------------------------------
// api/routers/config.py: a field left out of a PUT keeps its value; null follows the default.

const FIELDS = [
  "local_time",
  "timezone",
  "weekdays",
  "reply_wait_seconds",
  "final_reply_wait_seconds",
];
const DEFAULTS = consoleData.checkinPreferences[0].defaults;
const stored = Object.fromEntries(
  consoleData.checkinPreferences.map((pref) => [
    pref.developer_id,
    Object.fromEntries(FIELDS.map((f) => [f, pref.inherited.includes(f) ? null : pref[f]])),
  ]),
);
const consents = Object.fromEntries(
  consoleData.roster.map((person) => [person.id, consoleData.consent(person.id).consent]),
);

function effective(id) {
  const own = stored[id] ?? {};
  return {
    developer_id: id,
    ...Object.fromEntries(FIELDS.map((f) => [f, own[f] ?? DEFAULTS[f]])),
    inherited: FIELDS.filter((f) => own[f] === null || own[f] === undefined),
    defaults: DEFAULTS,
    send: consoleData.checkinSend,
  };
}

function isZone(name) {
  try {
    new Intl.DateTimeFormat("en-GB", { timeZone: name });
    return true;
  } catch {
    return false;
  }
}

// ---- Data sources ----------------------------------------------------------------
// core/application/sync_status_service.py: Jira targets are saved queries per project
// and pod, Git targets are repositories, the calendar is read live (no sync), and
// the chat directory syncs now through POST /config/directory/sync.

const at = (time, day = "2026-10-06") => `${day}T${time}:00Z`;
const target = (scope, label, detail, health, synced, items) => ({
  scope,
  label,
  detail,
  configured: true,
  health,
  last_synced_at: synced,
  last_attempt_at: synced,
  last_outcome: synced ? "succeeded" : null,
  last_error: null,
  items_synced: items,
});
const UNREADABLE =
  "The provider could not be reached or returned a response the sync could not read.";
const syncStatus = {
  generated_at: at("09:00"),
  sources: [
    {
      source: "issue_tracker",
      provider: "jira",
      simulated: false,
      sync_enabled: true,
      schedule: "0 * * * *",
      stale_after_minutes: 180,
      target_origin: "runtime_config",
      health: "healthy",
      last_synced_at: at("09:00"),
      last_attempt_at: at("09:00"),
      last_error: null,
      newest_item_at: at("08:42"),
      config_error: null,
      provider_error: null,
      targets: [
        target(
          "query:project:project-checkout:8790955b8c86",
          "Project Checkout Revamp",
          'project = "CHK"',
          "healthy",
          at("09:00"),
          24,
        ),
        target(
          "query:project:project-identity:d67887006c99",
          "Project Identity Platform",
          'project = "IDP"',
          "healthy",
          at("09:00"),
          11,
        ),
        target(
          "query:pod:pod-payments:51ab07c2e1f0",
          "Pod Payments Pod",
          '(project = "CHK") AND (component = Payments)',
          "healthy",
          at("09:00"),
          14,
        ),
      ],
    },
    {
      source: "vcs",
      provider: "gitlab",
      simulated: false,
      sync_enabled: true,
      schedule: "*/15 * * * *",
      stale_after_minutes: 60,
      target_origin: "runtime_config",
      health: "failing",
      last_synced_at: at("08:45"),
      last_attempt_at: at("09:00"),
      last_error: UNREADABLE,
      newest_item_at: at("08:31"),
      config_error: null,
      provider_error: null,
      targets: [
        target(
          "repo:acme/checkout-api",
          "acme/checkout-api",
          "Linked to Checkout Revamp",
          "healthy",
          at("08:45"),
          7,
        ),
        {
          ...target(
            "repo:acme/storefront-web",
            "acme/storefront-web",
            "Linked to Checkout Revamp",
            "failing",
            at("06:10"),
            0,
          ),
          last_attempt_at: at("09:00"),
          last_outcome: "failed",
          last_error: UNREADABLE,
        },
      ],
    },
    {
      source: "calendar",
      provider: "google",
      simulated: false,
      sync_enabled: false,
      schedule: null,
      stale_after_minutes: null,
      target_origin: "none",
      health: "disabled",
      last_synced_at: null,
      last_attempt_at: null,
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
      schedule: "0 */6 * * *",
      stale_after_minutes: 1080,
      target_origin: "workspace",
      health: "stale",
      last_synced_at: at("06:00", "2026-10-05"),
      last_attempt_at: at("06:00", "2026-10-05"),
      last_error: null,
      newest_item_at: null,
      config_error: null,
      provider_error: null,
      targets: [
        target(
          "workspace",
          "Chat workspace members",
          null,
          "stale",
          at("06:00", "2026-10-05"),
          consoleData.roster.length,
        ),
      ],
    },
  ],
};

function directorySynced() {
  const directory = syncStatus.sources.find((s) => s.source === "directory");
  const when = now();
  Object.assign(directory, { health: "healthy", last_synced_at: when, last_attempt_at: when });
  Object.assign(directory.targets[0], {
    health: "healthy",
    last_synced_at: when,
    last_attempt_at: when,
    last_outcome: "succeeded",
    items_synced: consoleData.roster.length,
  });
}

// ---- Delivery stages -------------------------------------------------------------
// core/domain/delivery.py: the default mapping, and a status no step names is
// placed by its broad state.

const STAGES = [
  "raised",
  "groomed",
  "in_development",
  "in_testing",
  "business_testing",
  "production",
];
const STAGE_LABELS = {
  raised: "Raised",
  groomed: "Groomed",
  in_development: "In development",
  in_testing: "In testing",
  business_testing: "Business testing",
  production: "Production",
};
const DEFAULT_MAPPING = {
  stages: {
    raised: ["Open", "New", "To Do", "Backlog", "Funnel"],
    groomed: ["Groomed", "Refined", "Ready", "Ready for Development", "Selected for Development"],
    in_development: ["In Progress", "In Development", "In Review", "Code Review"],
    in_testing: ["Ready for QA", "In QA", "QA", "Testing", "In Testing"],
    business_testing: ["UAT", "In UAT", "Business Testing", "Acceptance", "Business Acceptance"],
    production: ["Done", "Closed", "Resolved", "Released", "Deployed", "In Production"],
  },
  excluded_statuses: ["Won't Do", "Won't Fix", "Cancelled", "Canceled", "Rejected", "Duplicate"],
  requirement_types: [],
};
let mapping = clone(DEFAULT_MAPPING);
let mappingSaved = null; // { at, by } once saved
const FALLBACK = {
  todo: "raised",
  in_progress: "in_development",
  blocked: "in_development",
  done: "production",
};
// status, issues, issue types, broad state
const TRACKER = [
  ["Done", 11, ["Bug", "Story"], "done"],
  ["To Do", 9, ["Bug", "Story"], "todo"],
  ["In Progress", 6, ["Story"], "in_progress"],
  ["Code Review", 3, ["Story"], "in_progress"],
  ["Ready for QA", 2, ["Story"], "in_progress"],
  ["UAT", 2, ["Story"], "in_progress"],
  ["Waiting for Vendor", 2, ["Story"], "in_progress"],
  ["Parked", 1, ["Bug"], "todo"],
  ["Won't Do", 1, ["Bug"], "done"],
];

function observed(byMapping) {
  return TRACKER.map(([status, issues, issue_types, state]) => {
    const wanted = key(status);
    if ((byMapping.excluded_statuses ?? []).some((n) => key(n) === wanted)) {
      return { status, issues, issue_types, stage: null, mapped: true };
    }
    const stage = STAGES.find((s) => (byMapping.stages?.[s] ?? []).some((n) => key(n) === wanted));
    return stage
      ? { status, issues, issue_types, stage, mapped: true }
      : { status, issues, issue_types, stage: FALLBACK[state] ?? "raised", mapped: false };
  });
}

/** validated_mapping: a status sits in one step only, and an excluded one in none. */
function mappingProblems(body) {
  const problems = [];
  const seen = new Map();
  for (const stage of STAGES) {
    for (const name of body.stages?.[stage] ?? []) {
      const owner = seen.get(key(name));
      if (owner) problems.push(`"${name}" is in both ${owner} and ${STAGE_LABELS[stage]}`);
      seen.set(key(name), STAGE_LABELS[stage]);
    }
  }
  for (const name of body.excluded_statuses ?? []) {
    const owner = seen.get(key(name));
    if (owner) problems.push(`"${name}" is in ${owner} and also not counted`);
  }
  return problems.length > 0 ? sentence(problems) : null;
}

function stagesResponse() {
  return {
    stages: STAGES.map((stage) => ({
      stage,
      label: STAGE_LABELS[stage],
      statuses: mapping.stages[stage] ?? [],
    })),
    excluded_statuses: mapping.excluded_statuses,
    requirement_types: mapping.requirement_types,
    is_default: mappingSaved === null,
    updated_at: mappingSaved?.at ?? null,
    updated_by: mappingSaved?.by ?? null,
  };
}

// ---- Gates -----------------------------------------------------------------------
// core/domain/gates.py default_templates; gate_service.py makes the defaults the
// tenant's own on the first change, and brings them back when none is left.

const DEFAULT_GATES = [
  {
    template_id: "business-acceptance",
    name: "Business acceptance",
    guards_stage: "production",
    kinds: [
      {
        key: "acceptance",
        label: "Acceptance criterion",
        sign_off_roles: ["po", "mgr"],
        evidence_required: false,
        headings: ["Acceptance criteria", "AC", "Business acceptance", "Definition of done"],
        gherkin: false,
      },
    ],
    issue_types: [],
    enabled: true,
  },
  {
    template_id: "engineering-delivery",
    name: "Engineering delivery",
    guards_stage: "business_testing",
    kinds: [
      {
        key: "test_case",
        label: "Test case",
        sign_off_roles: ["dev", "sm"],
        evidence_required: true,
        headings: ["Test cases", "Test case", "Test plan", "Tests"],
        gherkin: true,
      },
    ],
    issue_types: [],
    enabled: true,
  },
];
let gates = null; // null while the defaults are in use

function gateProblems(body) {
  const problems = [];
  if (!String(body.name ?? "").trim()) problems.push("a gate needs a name");
  if (!Array.isArray(body.kinds) || body.kinds.length === 0) {
    problems.push("a gate needs at least one kind of item");
  }
  const keys = new Set();
  for (const kind of body.kinds ?? []) {
    if (!kind.key || !String(kind.label ?? "").trim()) {
      problems.push("every kind needs a key and a label");
    } else if (keys.has(kind.key)) problems.push(`the kind '${kind.key}' appears twice`);
    else if (!kind.sign_off_roles?.length) problems.push(`say who signs off ${kind.label}`);
    keys.add(kind.key);
  }
  return problems.length > 0 ? sentence(problems) : null;
}

// ---- Escalation ------------------------------------------------------------------
// core/domain/escalation_matrix.py: levels in order, named members that exist. The
// tenant's matrix is the one Overall reads; its named level is a member, so every
// level reads by name and saving it unchanged passes the member rule.

const level = (label, source, member_id, after_days) => ({ label, source, member_id, after_days });
const defaultMatrix = () => ({
  decision_owner_id: null,
  levels: [
    level("Scrum master", "team_scrum_master", null, { fix: 2, decision: 2, answer: 3, review: 2 }),
    level("Manager", "team_manager", null, { fix: 5, decision: 4, answer: 6, review: 5 }),
  ],
  updated_at: null,
  updated_by: null,
});
let tenantMatrix = {
  decision_owner_id: "U1003",
  levels: [
    level("Scrum master", "team_scrum_master", null, { fix: 2, decision: 2, answer: 3, review: 2 }),
    level("Manager", "team_manager", null, { fix: 4, decision: 4, answer: 6, review: 5 }),
    level("Engineering manager", "member", "U1001", { fix: 10, decision: 8 }),
  ],
  updated_at: "2026-09-01T09:00:00Z",
  updated_by: "U1001",
};
const projectMatrices = {};
const NEEDS = ["fix", "decision", "answer", "review"];

function matrixResponse(projectId) {
  const own = projectId ? projectMatrices[projectId] : null;
  const source = own ? "project" : tenantMatrix ? "tenant" : "default";
  return { project_id: projectId, source, ...clone(own ?? tenantMatrix ?? defaultMatrix()) };
}

function matrixProblems(body) {
  const problems = [];
  const levels = body.levels ?? [];
  if (levels.length > 5) problems.push("a matrix has at most 5 levels above the owner");
  levels.forEach((item, index) => {
    if (!String(item.label ?? "").trim()) problems.push(`level ${index + 2} needs a name`);
    if (item.source === "member" && !item.member_id) {
      problems.push(`level ${index + 2} needs the member it goes to`);
    }
  });
  for (const need of NEEDS) {
    let last = null;
    levels.forEach((item, index) => {
      const days = item.after_days?.[need];
      if (days === undefined) return;
      if (last && days < last[1]) {
        problems.push(`level ${index + 2} is reached before level ${last[0]} for ${need}`);
      }
      last = [index + 2, days];
    });
  }
  if (problems.length > 0) return sentence(problems);
  const memberIds = new Set(members().map((m) => m.id));
  const named = [
    body.decision_owner_id,
    ...levels.map((item) => (item.source === "member" ? item.member_id : null)),
  ].filter(Boolean);
  const unknown = named.filter((id) => !memberIds.has(id));
  return unknown.length > 0 ? `No member ${unknown.map((id) => `'${id}'`).join(", ")}.` : null;
}

// ---- Integrations ----------------------------------------------------------------
// infra/adapters/connections/specs.py and connection_service.py's save rules.
// Secret values are never kept: only which secret keys hold one.

const when = (field, ...values) => ({ field, values });
const f = (key, label, kind, extra = {}) => ({
  key,
  label,
  kind,
  required: false,
  help: "",
  placeholder: "",
  default: null,
  options: [],
  shown_when: null,
  ...extra,
});
const opt = (value, label, shown_when = null) => ({ value, label, shown_when });
const SPECS = [
  {
    connector: "jira",
    name: "Jira",
    description: "Issues, statuses, sprints and assignees for every project and pod.",
    purposes: ["issue_tracker"],
    exclusive_group: null,
    fields: [
      f("deployment", "Jira type", "select", {
        required: true,
        default: "cloud",
        options: [
          opt("cloud", "Jira Cloud (atlassian.net)"),
          opt("data_center", "Jira Data Center or Server"),
        ],
      }),
      f("base_url", "Jira address", "url", {
        required: true,
        placeholder: "https://your-company.atlassian.net",
        help: "The address you open Jira at, without a path.",
      }),
      f("auth_method", "Sign-in method", "select", {
        required: true,
        default: "api_token",
        options: [
          opt("api_token", "Email and API token", when("deployment", "cloud")),
          opt("personal_access_token", "Personal access token", when("deployment", "data_center")),
          opt("basic", "User name and password", when("deployment", "data_center")),
        ],
      }),
      f("email", "Account email", "email", {
        required: true,
        help: "The Atlassian account the API token belongs to.",
        shown_when: when("auth_method", "api_token"),
      }),
      f("api_token", "API token", "secret", {
        required: true,
        help: "Created at id.atlassian.com under Security, API tokens.",
        shown_when: when("auth_method", "api_token"),
      }),
      f("personal_access_token", "Personal access token", "secret", {
        required: true,
        help: "Created in Jira under Profile, Personal Access Tokens. Read access is enough.",
        shown_when: when("auth_method", "personal_access_token"),
      }),
      f("username", "User name", "text", {
        required: true,
        shown_when: when("auth_method", "basic"),
      }),
      f("password", "Password", "secret", {
        required: true,
        shown_when: when("auth_method", "basic"),
      }),
      f("story_points_field", "Story points field", "text", {
        placeholder: "customfield_10016",
        help: "The custom field that holds story points. Leave it empty to count requirements instead.",
      }),
    ],
  },
  {
    connector: "gitlab",
    name: "GitLab",
    description: "Repositories, commits and merge requests.",
    purposes: ["code"],
    exclusive_group: "code",
    fields: [
      f("base_url", "GitLab address", "url", {
        required: true,
        default: "https://gitlab.com",
        help: "The address you open GitLab at. The API path is added for you.",
      }),
      f("token", "Access token", "secret", {
        required: true,
        help: "A personal, group or project access token with the read_api scope.",
      }),
      f("namespace_id", "Group", "text", {
        placeholder: "acme or 1234",
        help: "The group to read repositories from, by path or id. Empty reads every repository the token can see.",
      }),
    ],
  },
  {
    connector: "github",
    name: "GitHub",
    description: "Repositories, commits and pull requests.",
    purposes: ["code"],
    exclusive_group: "code",
    fields: [
      f("base_url", "API address", "url", {
        required: true,
        default: "https://api.github.com",
        help: "Leave as is for github.com. GitHub Enterprise uses https://<host>/api/v3.",
      }),
      f("token", "Access token", "secret", {
        required: true,
        help: "A fine-grained token with read access to contents and pull requests.",
      }),
      f("owner", "Organisation or user", "text", {
        help: "Whose repositories to read. Empty reads the token owner's.",
      }),
    ],
  },
  {
    connector: "slack",
    name: "Slack",
    description: "Check-in messages, the member directory, and day reports to channels.",
    purposes: ["chat", "report_delivery"],
    exclusive_group: null,
    fields: [
      f("bot_token", "Bot token", "secret", {
        required: true,
        placeholder: "xoxb-…",
        help: "From the Slack app's OAuth & Permissions page.",
      }),
      f("app_token", "App-level token", "secret", {
        placeholder: "xapp-…",
        help: "Needed when replies arrive over Socket Mode (scope connections:write).",
      }),
      f("signing_secret", "Signing secret", "secret", {
        help: "Needed when Slack sends replies to OpenProgram's web address.",
      }),
    ],
  },
  {
    connector: "email",
    name: "Email (SMTP)",
    description: "Sends day reports to mailing lists and people by email.",
    purposes: ["report_delivery"],
    exclusive_group: null,
    fields: [
      f("host", "SMTP server", "text", { required: true, placeholder: "smtp.example.com" }),
      f("port", "Port", "number", { required: true, default: "587" }),
      f("security", "Encryption", "select", {
        required: true,
        default: "starttls",
        options: [
          opt("starttls", "STARTTLS (usually port 587)"),
          opt("ssl", "TLS from the start (usually port 465)"),
          opt("none", "None (internal relays only)"),
        ],
      }),
      f("username", "User name", "text", {
        help: "Leave empty for a relay that needs no sign-in.",
      }),
      f("password", "Password", "secret"),
      f("from_address", "From address", "email", {
        required: true,
        placeholder: "openprogram@example.com",
      }),
      f("from_name", "From name", "text", { default: "OpenProgram" }),
    ],
  },
  {
    connector: "teams",
    name: "Microsoft Teams",
    description: "Posts day reports to a Teams channel through a webhook.",
    purposes: ["report_delivery"],
    exclusive_group: null,
    fields: [
      f("webhook_url", "Channel webhook URL", "secret", {
        required: true,
        help: "From the channel's Workflows. Anyone with the URL can post, so it is kept as a secret.",
      }),
      f("channel_name", "Channel name", "text", {
        placeholder: "Delivery – daily",
        help: "Shown when you pick where a report goes.",
      }),
    ],
  },
  {
    connector: "google_calendar",
    name: "Google Calendar",
    description: "Out-of-office days, so a missing check-in on leave is not read as silence.",
    purposes: ["calendar"],
    exclusive_group: null,
    fields: [
      f("base_url", "API address", "url", {
        required: true,
        default: "https://www.googleapis.com/calendar/v3",
      }),
      f("token", "Access token", "secret", { required: true }),
      f("calendar_id", "Calendar", "text", {
        help: "A shared leave calendar's id. Empty reads each person's primary calendar.",
      }),
    ],
  },
];
const connections = {
  jira: {
    enabled: true,
    settings: {
      deployment: "data_center",
      base_url: "https://jira.acme.example",
      auth_method: "personal_access_token",
      story_points_field: "customfield_10016",
    },
    secret_keys: ["personal_access_token"],
    updated_at: "2026-09-28T10:12:00Z",
    updated_by: "U1001",
    last_test: { ok: true, message: "Connected to Jira.", tested_at: "2026-09-28T10:13:00Z" },
  },
  gitlab: {
    enabled: true,
    settings: { base_url: "https://gitlab.acme.example", namespace_id: "acme" },
    secret_keys: ["token"],
    updated_at: "2026-09-28T10:20:00Z",
    updated_by: "U1001",
    last_test: { ok: true, message: "Connected to GitLab.", tested_at: "2026-09-28T10:21:00Z" },
  },
  slack: {
    enabled: true,
    settings: {},
    secret_keys: ["bot_token", "app_token"],
    updated_at: "2026-09-20T08:00:00Z",
    // No member's id: the card shows the id, never a guessed name.
    updated_by: "ops-deploy",
    last_test: null,
  },
};
const ENVIRONMENT = new Set(["google_calendar"]);

function connectionResponse(spec) {
  const c = connections[spec.connector];
  return {
    ...clone(spec),
    configured: Boolean(c),
    enabled: c?.enabled ?? false,
    settings: c ? clone(c.settings) : {},
    secrets_set: c ? [...c.secret_keys].sort() : [],
    environment_configured: ENVIRONMENT.has(spec.connector),
    updated_at: c?.updated_at ?? null,
    updated_by: c?.updated_by ?? null,
    updated_by_name: c ? (members().find((m) => m.id === c.updated_by)?.name ?? null) : null,
    last_test: c?.last_test ?? null,
  };
}

const holds = (cond, values) => !cond || cond.values.includes(values[cond.field] ?? "");
const withDefaults = (spec, settings) =>
  Object.fromEntries(
    spec.fields
      .filter((field) => field.kind !== "secret")
      .map((field) => [field.key, settings[field.key] || field.default || ""])
      .filter(([, value]) => value),
  );

// The fields that decide where a stored secret goes or whom it signs in as
// (ConnectorField.routes_secrets in infra/adapters/connections/specs.py).
const ROUTES_SECRETS = {
  jira: ["base_url", "auth_method", "email", "username"],
  gitlab: ["base_url"],
  github: ["base_url"],
  google_calendar: ["base_url"],
  email: ["host", "port", "security", "username"],
};

const labelList = (labels) =>
  labels.length > 1 ? `${labels.slice(0, -1).join(", ")} and ${labels.at(-1)}` : (labels[0] ?? "");

/**
 * What ConnectionService.test does with a draft before any tester runs: a secret
 * typed is used; one mapped to null or blank is cleared for the test and left out;
 * one left out is the stored one, but only while every field that routes it is as
 * saved (else a 400 naming the fields); a required field still empty is "Fill in X
 * first." Returns [status, body] when it stops there, else null.
 */
function draftStop(spec, saved, settings, secrets) {
  const typed = new Set(
    Object.entries(secrets).flatMap(([key, value]) => (String(value ?? "").trim() ? [key] : [])),
  );
  const cleared = new Set(Object.keys(secrets).filter((key) => !typed.has(key)));
  const stored = new Set(saved?.secret_keys ?? []);
  const applicable = spec.fields.filter((field) => holds(field.shown_when, settings));
  const reused = applicable.filter(
    (field) =>
      field.kind === "secret" &&
      !typed.has(field.key) &&
      !cleared.has(field.key) &&
      stored.has(field.key),
  );
  const before = withDefaults(spec, saved?.settings ?? {});
  const after = withDefaults(spec, settings);
  const moved = applicable.filter(
    (field) =>
      (ROUTES_SECRETS[spec.connector] ?? []).includes(field.key) &&
      (before[field.key] ?? "") !== (after[field.key] ?? ""),
  );
  if (reused.length > 0 && moved.length > 0) {
    return [
      400,
      {
        detail: `${labelList(moved.map((f) => f.label))} changed. A stored secret is used only with the address and sign-in it was saved with, so enter ${labelList(reused.map((f) => f.label))} again to test the new values.`,
      },
    ];
  }
  const missing = applicable
    .filter(
      (field) =>
        field.required &&
        (field.kind === "secret"
          ? !(typed.has(field.key) || (stored.has(field.key) && !cleared.has(field.key)))
          : !after[field.key]),
    )
    .map((field) => field.label);
  if (missing.length > 0) {
    return [
      200,
      {
        ok: false,
        message: `Fill in ${missing.join(", ")} first.`,
        details: [],
        suggestions: {},
        tested_at: now(),
        recorded: false,
      },
    ];
  }
  return null;
}

/** A fixed sentence, like the real testers: a .invalid host is never reached. */
function testResult(spec, settings, typedSecrets) {
  const values = withDefaults(spec, settings);
  const urls = Object.values({ ...values, ...typedSecrets }).filter((v) =>
    /^https?:\/\//.test(String(v)),
  );
  const host = urls.length > 0 ? new URL(urls[0]).hostname : null;
  if (spec.connector === "teams" && host?.endsWith(".invalid")) {
    return { ok: false, message: "Could not reach the Teams webhook.", details: [] };
  }
  if (host?.endsWith(".invalid")) {
    return { ok: false, message: `Could not reach ${spec.name} at ${host}.`, details: [] };
  }
  if (spec.connector === "email" && String(values.host ?? "").endsWith(".invalid")) {
    return { ok: false, message: "Could not reach the mail server.", details: [] };
  }
  return {
    ok: true,
    message: `Connected to ${spec.name}.`,
    details: host ? [{ label: "Server", value: host }] : [],
  };
}

// ---- Branding --------------------------------------------------------------------
// core/application/branding_service.py: PNG, JPEG or WebP by their own bytes, 256 KB.

// Starts with the shell mock's made-up mark, so the header shows a logo until it is removed.
let logo = clone(demoLogo);
function sniff(bytes) {
  const ascii = (start, end) => bytes.toString("ascii", start, end);
  if (bytes[0] === 0x89 && ascii(1, 4) === "PNG" && ascii(12, 16) === "IHDR") return "image/png";
  if (bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return "image/jpeg";
  if (ascii(0, 4) === "RIFF" && ascii(8, 12) === "WEBP") return "image/webp";
  return null;
}

// ---- Routing ---------------------------------------------------------------------

const CONFIG_PATHS = [
  /^\/config\/checkin-preferences$/,
  /^\/config\/members\/[^/]+\/(checkin-preference|writeback-consent)$/,
  /^\/admin\/ops\/sync-status$/,
  /^\/config\/directory\/sync$/,
  /^\/config\/delivery\//,
  /^\/config\/gates(\/|$)/,
  /^\/config\/escalation(\/|$)/,
  /^\/config\/integrations(\/|$)/,
  /^\/config\/branding\/logo$/,
];

/** Answers this lane's endpoints; true when it did. A write answers once its body is read. */
export function api(req, url, roles, actingAs, send, deny) {
  const p = url.pathname;
  const method = req.method ?? "GET";
  // Who saved a change; the mock acts as Asha when no one is named, like mock-api.
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

  // Anyone signed in reads the logo: the header shows it on every screen.
  if (p === "/config/branding" && method === "GET") return done(200, { logo });
  if (!CONFIG_PATHS.some((pattern) => pattern.test(p))) return false;
  if (!roles.includes("admin")) {
    deny();
    return true;
  }

  // Branding.
  if (p === "/config/branding/logo" && method === "DELETE") {
    logo = null;
    return done(204, null);
  }
  if (p === "/config/branding/logo") {
    return write((body) => {
      const bytes = Buffer.from(String(body.data_base64 ?? ""), "base64");
      const type = sniff(bytes);
      if (bytes.length > 256 * 1024)
        return send(413, { detail: "The logo is larger than 256 KB." });
      if (!type || type !== body.content_type) {
        return send(415, { detail: "The logo must be a PNG, JPEG or WebP image." });
      }
      logo = {
        data_url: `data:${type};base64,${bytes.toString("base64")}`,
        content_type: type,
        sha256: "mock",
        updated_at: now(),
        updated_by: userId,
      };
      return send(200, { logo });
    });
  }

  // Members, and their check-ins.
  if (p === "/config/checkin-preferences") {
    return done(
      200,
      members().map((member) => effective(member.id)),
    );
  }
  if ((m = p.match(/^\/config\/members\/([^/]+)\/checkin-preference$/))) {
    const id = m[1];
    if (method === "GET") return done(200, effective(id));
    return write((body) => {
      if (Array.isArray(body.weekdays) && body.weekdays.length === 0) {
        return send(
          422,
          refused(
            "weekdays",
            "weekdays needs at least one day: with none, this person is never asked to check in",
          ),
        );
      }
      if (typeof body.timezone === "string" && !isZone(body.timezone)) {
        return send(422, refused("timezone", "timezone must be a valid IANA timezone"));
      }
      const own = (stored[id] ??= {});
      for (const field of FIELDS) if (field in body) own[field] = body[field];
      return send(200, effective(id));
    });
  }
  if ((m = p.match(/^\/config\/members\/([^/]+)\/writeback-consent$/))) {
    const id = m[1];
    if (method === "GET")
      return done(200, { developer_id: id, consent: consents[id] ?? "always_ask" });
    return write((body) => {
      consents[id] = body.consent;
      return send(200, { developer_id: id, consent: body.consent });
    });
  }

  // Data sources.
  if (p === "/admin/ops/sync-status")
    return done(200, { ...clone(syncStatus), generated_at: now() });
  if (p === "/config/directory/sync") {
    return write(() => {
      directorySynced();
      return send(200, {
        tenant_id: TENANT,
        synced_count: consoleData.roster.length,
        deactivated_count: 0,
      });
    });
  }

  // Delivery stages.
  if (p === "/config/delivery/stages" && method === "GET") return done(200, stagesResponse());
  if (p === "/config/delivery/stages") {
    return write((body) => {
      const problem = mappingProblems(body);
      if (problem) return send(422, { detail: problem });
      mapping = {
        stages: Object.fromEntries(STAGES.map((s) => [s, body.stages?.[s] ?? []])),
        excluded_statuses: body.excluded_statuses ?? [],
        requirement_types: body.requirement_types ?? [],
      };
      mappingSaved = { at: now(), by: userId };
      return send(200, stagesResponse());
    });
  }
  if (p === "/config/delivery/statuses") return done(200, observed(mapping));
  if (p === "/config/delivery/statuses/preview") {
    return write((body) => {
      const problem = mappingProblems(body);
      return problem ? send(422, { detail: problem }) : send(200, observed(body));
    });
  }

  // Gates.
  if (p === "/config/gates" && method === "GET") {
    return done(200, { templates: clone(gates ?? DEFAULT_GATES), is_default: gates === null });
  }
  if (p === "/config/gates") {
    return write((body) => {
      const problem = gateProblems(body);
      if (problem) return send(422, { detail: problem });
      const saved = { ...body, template_id: body.template_id || `gate-${Date.now().toString(36)}` };
      gates = [
        ...(gates ?? clone(DEFAULT_GATES)).filter((g) => g.template_id !== saved.template_id),
        saved,
      ].sort((a, b) => a.name.localeCompare(b.name));
      return send(200, saved);
    });
  }
  if ((m = p.match(/^\/config\/gates\/([^/]+)$/)) && method === "DELETE") {
    const rest = (gates ?? clone(DEFAULT_GATES)).filter(
      (g) => g.template_id !== decodeURIComponent(m[1]),
    );
    // With none saved, the defaults come back.
    gates = rest.length > 0 ? rest : null;
    return done(204, null);
  }

  // Escalation.
  if (p === "/config/escalation") {
    return done(200, {
      tenant: matrixResponse(""),
      projects: Object.keys(projectMatrices)
        .sort()
        .map((id) => matrixResponse(id)),
    });
  }
  if ((m = p.match(/^\/config\/escalation\/(tenant|projects\/([^/]+))$/))) {
    const projectId = m[2] ? decodeURIComponent(m[2]) : "";
    if (method === "GET") return done(200, matrixResponse(projectId));
    if (method === "DELETE") {
      delete projectMatrices[projectId];
      return done(204, null);
    }
    return write((body) => {
      const problem = matrixProblems(body);
      if (problem) return send(422, { detail: problem });
      const saved = {
        decision_owner_id: body.decision_owner_id || null,
        levels: (body.levels ?? []).map((item) => ({
          ...item,
          member_id: item.source === "member" ? item.member_id : null,
        })),
        updated_at: now(),
        updated_by: userId,
      };
      if (projectId) projectMatrices[projectId] = saved;
      else tenantMatrix = saved;
      return send(200, matrixResponse(projectId));
    });
  }

  // Integrations.
  if (p === "/config/integrations") return done(200, SPECS.map(connectionResponse));
  if ((m = p.match(/^\/config\/integrations\/([^/]+)(\/test)?$/))) {
    const spec = SPECS.find((s) => s.connector === decodeURIComponent(m[1]));
    if (!spec) return done(404, { detail: `No connector '${m[1]}' is available.` });
    if (m[2]) {
      return write((body) => {
        const saved = connections[spec.connector];
        const draft =
          Object.keys(body.settings ?? {}).length > 0 || Object.keys(body.secrets ?? {}).length > 0;
        const settings = draft
          ? { ...(saved?.settings ?? {}), ...body.settings }
          : (saved?.settings ?? {});
        const stopped = draft ? draftStop(spec, saved, settings, body.secrets ?? {}) : null;
        if (stopped) return send(stopped[0], stopped[1]);
        const typedSecrets = Object.fromEntries(
          Object.entries(body.secrets ?? {}).filter(([, value]) => String(value ?? "").trim()),
        );
        const result = {
          ...testResult(spec, settings, typedSecrets),
          suggestions: {},
          tested_at: now(),
          recorded: !draft,
        };
        if (!draft && saved) {
          saved.last_test = { ok: result.ok, message: result.message, tested_at: result.tested_at };
        }
        return send(200, result);
      });
    }
    if (method === "GET") return done(200, connectionResponse(spec));
    if (method === "DELETE") {
      delete connections[spec.connector];
      return done(204, null);
    }
    return write((body) => {
      const settings = {};
      for (const [field, value] of Object.entries(body.settings ?? {})) {
        const known = spec.fields.find((item) => item.key === field);
        if (!known) return send(422, { detail: `${spec.name} has no field '${field}'.` });
        if (known.kind === "secret") {
          return send(422, { detail: `${known.label} is a secret and is set on its own.` });
        }
        if (value && String(value).trim()) settings[field] = String(value).trim();
      }
      const keys = new Set(connections[spec.connector]?.secret_keys ?? []);
      for (const [field, value] of Object.entries(body.secrets ?? {})) {
        const known = spec.fields.find((item) => item.key === field);
        if (known?.kind !== "secret") {
          return send(422, { detail: `${spec.name} has no secret '${field}'.` });
        }
        if (value && String(value).trim()) keys.add(field);
        else keys.delete(field);
      }
      if (body.enabled) {
        const values = withDefaults(spec, settings);
        const missing = spec.fields
          .filter((item) => item.required && holds(item.shown_when, values))
          .filter((item) => (item.kind === "secret" ? !keys.has(item.key) : !values[item.key]))
          .map((item) => item.label);
        if (missing.length > 0) {
          return send(422, {
            detail: `Fill in ${missing.join(", ")} before turning ${spec.name} on.`,
          });
        }
        const other = SPECS.find(
          (s) =>
            s.connector !== spec.connector &&
            s.exclusive_group &&
            s.exclusive_group === spec.exclusive_group &&
            connections[s.connector]?.enabled,
        );
        if (other) {
          return send(409, {
            detail: `${other.name} is already on. Turn it off before turning ${spec.name} on.`,
          });
        }
      }
      connections[spec.connector] = {
        enabled: Boolean(body.enabled),
        settings,
        secret_keys: [...keys],
        updated_at: now(),
        updated_by: userId,
        last_test: null,
      };
      return send(200, connectionResponse(spec));
    });
  }
  return false;
}
