// Mock for the Admin structure screens (Entities, Links, Directory, Escalation contacts): the
// /config entity, link, directory, identity and escalation endpoints, with state that changes as
// the screens write, so every write can be round-tripped without a backend. NOT real data:
// shapes follow src/api/generated.ts and the people are the demo roster in mock-console.mjs.
//
// The directory lists (/programs, /projects, /pods, /workstreams) belong to mock-console.mjs and
// the reports mock. This module changes those very arrays in place, so Delivery and Today in the
// mock show a new, linked or deleted node too. A workstream no task is in is kept out of the
// list and found only by a direct read, the way the backend treats an empty workstream.
//
// Wired in by scripts/mock-api.mjs with one import and one call. It returns true when it handled
// the request; a write answers once its body has been read.
import * as consoleData from "../mock-console.mjs";

const TENANT = "demo";
const KIND_WORDS = { program: "program", project: "project", pod: "pod", workstream: "workstream" };

const detail = (message) => ({ detail: message });
const notFound = (kind, id) => detail(`${kind} ${id} not found for tenant ${TENANT}`);
const add = (list, value) => {
  if (list.includes(value)) return false;
  list.push(value);
  return true;
};
const drop = (list, value) => {
  const index = list.indexOf(value);
  if (index < 0) return false;
  list.splice(index, 1);
  return true;
};

// ---- State ----------------------------------------------------------------------------------------

const roleOf = (person) =>
  person.roles.includes("sm")
    ? "scrum_master"
    : person.roles.includes("po")
      ? "product_owner"
      : person.roles.includes("mgr")
        ? "manager"
        : "developer";
const emailOf = (name) => `${name.toLowerCase().replace(/[^a-z]+/g, ".")}@example.com`;
const handleOf = (name) => name.split(" ")[0].toLowerCase();

const blankConfig = {
  description: null,
  code: null,
  jira_project_key: null,
  jira_base_jql: null,
  jira_board_id: null,
  jira_filter_jql: null,
  github_repos: [],
};

/** Members: the people the Check-ins tab already lists, with what the directory gives. */
const members = new Map(
  consoleData.configMembers.map((member) => {
    const person = consoleData.roster.find((p) => p.id === member.id);
    return [
      member.id,
      {
        ...member,
        ...blankConfig,
        metadata: {
          email: emailOf(person.name),
          title: person.title,
          handle: handleOf(person.name),
          app_roles: person.roles.join(","),
          chat_external_id: person.id,
        },
      },
    ];
  }),
);

/** What the config API holds beyond what the directory lists carry, by node id. */
const extra = new Map([
  [
    "project-checkout",
    {
      code: "CHK",
      jira_project_key: "CHK",
      github_repos: ["acme/checkout-api", "acme/storefront-web"],
    },
  ],
  [
    "project-identity",
    { code: "IDP", jira_project_key: "IDP", github_repos: ["acme/identity-service"] },
  ],
  [
    "project-insights",
    { code: "INS", jira_project_key: "INS", github_repos: ["acme/insights-pipeline"] },
  ],
  ["pod-payments", { github_repos: ["acme/checkout-api"] }],
  ["program-digital", { description: "All customer-facing platform delivery" }],
]);

const seededWorkstreams = new Set(consoleData.workstreams.map((ws) => ws.id));
const emptyWorkstreams = new Map();
const workstreamTasks = new Map(); // workstream id -> task ids put there by hand

/** The tasks the pods hold, and who each is assigned to. */
const taskNames = new Map();
const assignees = new Map(); // task id -> member ids
for (const pod of consoleData.pods) {
  for (const task of consoleData.podTasks(pod.id).tasks) {
    taskNames.set(task.id, task.name);
    assignees.set(
      task.id,
      task.owners.map((owner) => owner.id),
    );
  }
}

/** A person's role in each pod, from what they do in the console. */
const podRoles = new Map(
  consoleData.pods.map((pod) => [
    pod.id,
    new Map(pod.member_ids.map((id) => [id, roleOf(consoleData.roster.find((p) => p.id === id))])),
  ]),
);

/** Which accounts each member is: every demo member is fully linked; an import starts empty. */
const identity = new Map(
  [...members.values()].map((member) => [
    member.id,
    {
      developer_id: member.id,
      chat_user_id: member.id,
      jira_account_id: member.metadata.handle,
      jira_email: member.metadata.email,
      vcs_username: member.metadata.handle,
    },
  ]),
);
const IDENTITY_FIELDS = ["chat_user_id", "jira_account_id", "jira_email", "vcs_username"];
const linkOf = (memberId) =>
  identity.get(memberId) ?? {
    developer_id: memberId,
    chat_user_id: null,
    jira_account_id: null,
    jira_email: null,
    vcs_username: null,
  };
const chatIdOf = (memberId) => linkOf(memberId).chat_user_id;

const contactOf = (memberId) => ({
  chat_external_id: chatIdOf(memberId),
  display_name: members.get(memberId)?.name ?? null,
  member_id: memberId,
});
/** Two pods have contacts and two have none, so both a set and an unset pod show. */
const contacts = new Map(
  consoleData.pods.map((pod) => [pod.id, { scrum_master: null, manager: null }]),
);
contacts.set("pod-payments", { scrum_master: contactOf("U1006"), manager: contactOf("U1001") });
contacts.set("pod-storefront", { scrum_master: contactOf("U1014"), manager: null });

/** The chat directory: the demo roster. Everyone but the executive is already a member. */
const directoryUsers = consoleData.roster.map((person) => ({
  external_id: person.id,
  display_name: person.name,
  email: emailOf(person.name),
  handle: handleOf(person.name),
  avatar_url: null,
  title: person.title,
  is_active: true,
  source: "mock_slack",
  metadata: {},
}));

// ---- Reading the nodes ------------------------------------------------------------------------------

const lists = () => ({
  program: consoleData.programs,
  project: consoleData.projects,
  pod: consoleData.pods,
  workstream: consoleData.workstreams,
});
const dirItem = (kind, id) =>
  lists()[kind]?.find((item) => item.id === id) ??
  (kind === "workstream" ? emptyWorkstreams.get(id) : undefined);
const inUse = (id) => seededWorkstreams.has(id) || (workstreamTasks.get(id)?.length ?? 0) > 0;
const allWorkstreams = () => [...consoleData.workstreams, ...emptyWorkstreams.values()];

const configNode = (kind, id) => {
  if (kind === "member") return members.get(id);
  const item = dirItem(kind, id);
  if (!item) return undefined;
  const fields = { ...blankConfig, ...(extra.get(id) ?? {}) };
  return { id, kind, name: item.name, ...fields, metadata: { ...item.metadata } };
};
const configList = (kind) => {
  if (kind === "member") return [...members.values()].sort((a, b) => a.name.localeCompare(b.name));
  const items = kind === "workstream" ? allWorkstreams() : lists()[kind];
  return items.map((item) => configNode(kind, item.id));
};

/** The members as GET /config/members lists them: the admin-config mock reads them too. */
export const memberList = () => configList("member");

const refreshPeople = (item) => {
  item.people = ["owner_id", "tpm_id", "sm_id"]
    .filter((key) => typeof item.metadata[key] === "string" && item.metadata[key])
    .map((key) => {
      const id = item.metadata[key];
      const member = members.get(id);
      return { key, id, member_id: member ? member.id : null, name: member ? member.name : null };
    });
};

/** Keeps what the directory shows in step with the links: an empty workstream stays out of it. */
function refreshWorkstreams() {
  for (const ws of [...consoleData.workstreams]) {
    if (!inUse(ws.id)) {
      drop(consoleData.workstreams, ws);
      emptyWorkstreams.set(ws.id, ws);
    }
  }
  for (const ws of [...emptyWorkstreams.values()]) {
    if (inUse(ws.id)) {
      emptyWorkstreams.delete(ws.id);
      consoleData.workstreams.push(ws);
    }
  }
  for (const ws of allWorkstreams()) {
    ws.in_use = inUse(ws.id);
    ws.task_ids = [...(workstreamTasks.get(ws.id) ?? [])];
  }
  for (const project of consoleData.projects) {
    project.workstream_ids = consoleData.workstreams
      .filter((ws) => ws.project_ids.includes(project.id))
      .map((ws) => ws.id);
  }
  for (const pod of consoleData.pods) {
    pod.workstream_ids = consoleData.workstreams
      .filter((ws) => ws.pod_ids.includes(pod.id))
      .map((ws) => ws.id);
  }
}

const blankItem = (kind, id, name) => ({
  id,
  kind,
  name,
  description: null,
  code: null,
  metadata: {},
  rag: "unknown",
  source: "unknown",
  program_ids: [],
  project_ids: [],
  workstream_ids: [],
  pod_ids: [],
  member_ids: [],
  task_ids: [],
  people: [],
  in_use: true,
});

// ---- Writing the nodes ------------------------------------------------------------------------------

const hasNode = (id) =>
  Object.keys(KIND_WORDS).some((kind) => dirItem(kind, id)) || members.has(id);

/** The fields a PUT names, and only those: the backend merges a node's fields one by one. */
function applyFields(kind, id, body) {
  const item = dirItem(kind, id);
  const fields = extra.get(id) ?? {};
  if (typeof body.name === "string" && body.name.trim()) item.name = body.name.trim();
  for (const key of Object.keys(blankConfig)) {
    if (key === "github_repos") {
      if (key in body) fields.github_repos = body.github_repos ?? [];
    } else if (key in body) {
      fields[key] = body[key] === "" ? null : (body[key] ?? null);
    }
  }
  extra.set(id, fields);
  item.description = fields.description ?? null;
  item.code = fields.code ?? null;
  if (body.metadata && typeof body.metadata === "object") {
    Object.assign(item.metadata, body.metadata);
    if (kind === "workstream") refreshPeople(item);
  }
}

function createNode(kind, body) {
  const id = String(body.id ?? "").trim();
  const name = String(body.name ?? "").trim();
  if (!id || !name) {
    const loc = ["body", id ? "name" : "id"];
    const problem = {
      loc,
      msg: "String should have at least 1 character",
      type: "string_too_short",
    };
    return [422, { detail: [problem] }];
  }
  if (hasNode(id)) return [409, detail(`node ${id} already exists`)];
  const item = blankItem(kind, id, name);
  if (kind === "workstream") {
    item.in_use = false;
    emptyWorkstreams.set(id, item);
  } else {
    lists()[kind].push(item);
  }
  applyFields(kind, id, body);
  return [201, configNode(kind, id)];
}

function deleteNode(kind, id) {
  const item = dirItem(kind, id);
  if (!item) return [404, notFound(KIND_WORDS[kind], id)];
  for (const list of Object.values(lists())) drop(list, item);
  emptyWorkstreams.delete(id);
  const others = [...Object.values(lists()).flat(), ...emptyWorkstreams.values()];
  for (const other of others) {
    for (const key of ["program_ids", "project_ids", "workstream_ids", "pod_ids"])
      drop(other[key], id);
  }
  workstreamTasks.delete(id);
  contacts.delete(id);
  podRoles.delete(id);
  extra.delete(id);
  refreshWorkstreams();
  return [204, null];
}

// ---- Links ------------------------------------------------------------------------------------------------

const edge = (from, to, kind, metadata = {}) => ({
  from_node_id: from,
  to_node_id: to,
  kind,
  valid_from: "2026-10-06",
  valid_to: null,
  metadata,
});
const EXISTS = (kind) => ({ exists: kind });
const MISSING = (kind) => ({ missing: kind });

/** Each link adds or removes itself on both ends and says what it found. */
const links = {
  programProject: (programId, projectId, remove) => {
    const program = dirItem("program", programId);
    const project = dirItem("project", projectId);
    if (remove) {
      drop(project.program_ids, programId);
      return drop(program.project_ids, projectId) ? true : MISSING("contains");
    }
    if (!add(program.project_ids, projectId)) return EXISTS("contains");
    add(project.program_ids, programId);
    return edge(programId, projectId, "contains");
  },
  projectPod: (projectId, podId, remove) => {
    const project = dirItem("project", projectId);
    const pod = dirItem("pod", podId);
    if (remove) {
      drop(pod.project_ids, projectId);
      return drop(project.pod_ids, podId) ? true : MISSING("contains");
    }
    if (!add(project.pod_ids, podId)) return EXISTS("contains");
    add(pod.project_ids, projectId);
    return edge(projectId, podId, "contains");
  },
  projectWorkstream: (projectId, wsId, remove) => {
    const ws = dirItem("workstream", wsId);
    if (remove) return drop(ws.project_ids, projectId) ? true : MISSING("contains");
    return add(ws.project_ids, projectId) ? edge(projectId, wsId, "contains") : EXISTS("contains");
  },
  podWorkstream: (podId, wsId, remove) => {
    const ws = dirItem("workstream", wsId);
    if (remove) return drop(ws.pod_ids, podId) ? true : MISSING("assigned_to");
    return add(ws.pod_ids, podId) ? edge(podId, wsId, "assigned_to") : EXISTS("assigned_to");
  },
  workstreamTask: (wsId, taskId, remove) => {
    const tasks = workstreamTasks.get(wsId) ?? [];
    workstreamTasks.set(wsId, tasks);
    if (remove) return drop(tasks, taskId) ? true : MISSING("contains");
    return add(tasks, taskId) ? edge(wsId, taskId, "contains") : EXISTS("contains");
  },
  podMember: (podId, memberId, remove, role) => {
    const pod = dirItem("pod", podId);
    if (remove) {
      podRoles.get(podId)?.delete(memberId);
      return drop(pod.member_ids, memberId) ? true : MISSING("contains");
    }
    if (!add(pod.member_ids, memberId)) return EXISTS("contains");
    if (!podRoles.has(podId)) podRoles.set(podId, new Map());
    podRoles.get(podId).set(memberId, role);
    return edge(podId, memberId, "contains", { role });
  },
  memberTask: (memberId, taskId, remove) => {
    const owners = assignees.get(taskId) ?? [];
    assignees.set(taskId, owners);
    if (remove) return drop(owners, memberId) ? true : MISSING("assigned_to");
    return add(owners, memberId) ? edge(memberId, taskId, "assigned_to") : EXISTS("assigned_to");
  },
};

/** A 404 tuple when a node a link names does not exist; null when it does. */
function need(kind, id) {
  if (kind === "task") return taskNames.has(id) ? null : [404, notFound("task", id)];
  if (kind === "member") return members.has(id) ? null : [404, notFound("developer", id)];
  return dirItem(kind, id) ? null : [404, notFound(kind, id)];
}

/** [status, body] for one link request: both nodes must exist, then the link is made or removed. */
function changeLink([kindA, kindB], fn, [a, b], remove, extraArg, missingMessage) {
  const refused = need(kindA, a) ?? need(kindB, b);
  if (refused) return refused;
  const done = fn(a, b, remove, extraArg);
  if (done && typeof done === "object" && "exists" in done) {
    return [409, detail(`${done.exists} link already exists`)];
  }
  if (done && typeof done === "object" && "missing" in done) {
    return [404, detail(missingMessage ?? `${done.missing} link was not found`)];
  }
  refreshWorkstreams();
  return remove ? [204, null] : [200, done];
}

// ---- Identity, directory, escalation ---------------------------------------------------------------

function autoMatch() {
  const matched = [];
  for (const member of members.values()) {
    const user = directoryUsers.find((u) => u.external_id === member.id);
    if (!user) continue;
    const next = { ...linkOf(member.id) };
    const filled = [];
    if (!next.chat_user_id) {
      next.chat_user_id = user.external_id;
      filled.push("chat_user_id");
    }
    if (!next.jira_email && user.email) {
      next.jira_email = user.email;
      filled.push("jira_email");
    }
    if (!next.jira_account_id && next.jira_email) {
      next.jira_account_id = user.handle;
      filled.push("jira_account_id");
    }
    if (filled.length === 0) continue;
    identity.set(member.id, next);
    matched.push({ id: member.id, name: member.name, filled });
  }
  return { updated_count: matched.length, members: matched };
}

/** Both contacts are replaced on every save: one the request leaves out is cleared. */
function putContacts(podId, body) {
  const current = contacts.get(podId) ?? { scrum_master: null, manager: null };
  const next = {};
  for (const slot of ["scrum_master", "manager"]) {
    const choice = body[slot] ?? null;
    if (choice === null) {
      next[slot] = null;
    } else if (choice.member_id) {
      const member = members.get(choice.member_id);
      if (!member) return [404, detail(`member ${choice.member_id} was not found`)];
      if (!chatIdOf(member.id)) return [400, detail(`${member.name} has no chat ID linked`)];
      next[slot] = contactOf(member.id);
    } else if (choice.chat_external_id) {
      const owner = [...members.values()].find((m) => chatIdOf(m.id) === choice.chat_external_id);
      if (owner) next[slot] = contactOf(owner.id);
      else if (current[slot]?.chat_external_id === choice.chat_external_id)
        next[slot] = current[slot];
      else return [400, detail(`chat ID ${choice.chat_external_id} is not linked to any member`)];
    } else {
      return [400, detail("an escalation contact needs a member")];
    }
  }
  contacts.set(podId, next);
  return [200, { pod_id: podId, ...next }];
}

function importFromDirectory(body) {
  const imported = [];
  for (const id of body.external_ids ?? []) {
    const user = directoryUsers.find((u) => u.external_id === id && u.is_active);
    if (!user) return [404, detail(`active directory user ${id} not found for tenant ${TENANT}`)];
    if (!members.has(id)) {
      members.set(id, {
        id,
        kind: "developer",
        name: user.display_name,
        ...blankConfig,
        metadata: {
          email: user.email,
          title: user.title,
          handle: user.handle,
          chat_external_id: id,
        },
      });
    }
    imported.push(members.get(id));
  }
  return [201, imported];
}

// ---- The routes --------------------------------------------------------------------------------------------

const readBody = (req) =>
  new Promise((resolve) => {
    let raw = "";
    req.on("data", (chunk) => {
      raw += chunk;
    });
    req.on("end", () => {
      try {
        resolve(raw ? JSON.parse(raw) : {});
      } catch {
        resolve(null);
      }
    });
  });

const KINDS = {
  programs: "program",
  projects: "project",
  pods: "pod",
  workstreams: "workstream",
  members: "member",
};
const dec = decodeURIComponent;

/** Answers an Admin structure endpoint, or returns false when the path is not one of these. */
export function api(req, url, roles, userId, send, deny) {
  const p = url.pathname;
  const method = req.method ?? "GET";
  let m;

  // The directory's direct read of one workstream, in use or not.
  if ((m = p.match(/^\/workstreams\/([^/]+)$/)) && method === "GET") {
    const ws = dirItem("workstream", dec(m[1]));
    if (ws) send(200, ws);
    else send(404, notFound("workstream", dec(m[1])));
    return true;
  }
  // A pod's tasks, with who each is assigned to now.
  if ((m = p.match(/^\/pods\/([^/]+)\/tasks$/)) && method === "GET") {
    if (!roles.some((r) => ["sm", "mgr", "admin"].includes(r))) return false;
    const base = consoleData.podTasks(dec(m[1]));
    const owners = (task) =>
      (assignees.get(task.id) ?? []).map((id) => ({ id, name: members.get(id)?.name ?? id }));
    send(200, { ...base, tasks: base.tasks.map((task) => ({ ...task, owners: owners(task) })) });
    return true;
  }

  const mine =
    /^\/config\/(programs|projects|pods|workstreams|members)(\/|$)/.test(p) ||
    p.startsWith("/config/directory/");
  if (!mine) return false;
  if (!roles.includes("admin")) {
    deny();
    return true;
  }
  /** A write: read the body, then answer with [status, body] from the handler. */
  const write = (handler) => {
    void readBody(req).then((body) => {
      if (body === null) return send(422, detail("The request body is not valid JSON."));
      const [status, payload] = handler(body);
      return send(status, payload);
    });
    return true;
  };
  const answer = ([status, payload]) => {
    send(status, payload);
    return true;
  };

  // Lists and creation.
  if ((m = p.match(/^\/config\/(programs|projects|pods|workstreams|members)$/))) {
    const kind = KINDS[m[1]];
    if (method === "GET") {
      send(200, configList(kind));
      return true;
    }
    if (method === "POST" && kind !== "member") return write((body) => createNode(kind, body));
    return false;
  }

  // Members: the directory import and the identity links come before the one-member routes.
  if (p === "/config/members/unmapped" && method === "GET") {
    const stuck = [...members.values()].filter((member) => !chatIdOf(member.id));
    send(
      200,
      stuck.map((member) => ({
        id: member.id,
        name: member.name,
        missing: IDENTITY_FIELDS.filter((field) => !linkOf(member.id)[field]),
      })),
    );
    return true;
  }
  if (p === "/config/members/identity-links/auto-match" && method === "POST") {
    send(200, autoMatch());
    return true;
  }
  if (p === "/config/members/from-directory" && method === "POST")
    return write(importFromDirectory);
  if ((m = p.match(/^\/config\/members\/([^/]+)\/identity-link$/))) {
    const id = dec(m[1]);
    if (!members.has(id)) return answer([404, notFound("developer", id)]);
    if (method === "GET") return answer([200, linkOf(id)]);
    if (method === "PUT") {
      return write((body) => {
        const next = { ...linkOf(id) };
        for (const field of IDENTITY_FIELDS) if (field in body) next[field] = body[field] || null;
        identity.set(id, next);
        return [200, next];
      });
    }
  }
  if ((m = p.match(/^\/config\/members\/([^/]+)\/tasks$/))) {
    const id = dec(m[1]);
    const kinds = ["member", "task"];
    if (method === "POST") {
      return write((body) =>
        changeLink(kinds, links.memberTask, [id, String(body.task_id ?? "")], false),
      );
    }
    if (method === "DELETE") {
      const taskId = url.searchParams.get("task_id") ?? "";
      return answer(changeLink(kinds, links.memberTask, [id, taskId], true));
    }
  }

  // The chat directory.
  if (p === "/config/directory/users" && method === "GET") {
    const query = (url.searchParams.get("query") ?? "").toLowerCase();
    const limit = Number(url.searchParams.get("limit") ?? 25);
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const matching = directoryUsers.filter((user) =>
      `${user.display_name} ${user.email} ${user.handle}`.toLowerCase().includes(query),
    );
    send(200, { items: matching.slice(offset, offset + limit), total: matching.length });
    return true;
  }
  if (p === "/config/directory/sync" && method === "POST") {
    const source = consoleData.syncStatus.sources.find((item) => item.source === "directory");
    if (source) {
      source.last_synced_at = new Date().toISOString();
      source.last_attempt_at = source.last_synced_at;
      source.health = "healthy";
    }
    send(200, { tenant_id: TENANT, synced_count: directoryUsers.length, deactivated_count: 0 });
    return true;
  }

  // One node: change or delete.
  if ((m = p.match(/^\/config\/(programs|projects|pods|workstreams)\/([^/]+)$/))) {
    const kind = KINDS[m[1]];
    const id = dec(m[2]);
    if (method === "PUT") {
      return write((body) => {
        if (!dirItem(kind, id)) return [404, notFound(KIND_WORDS[kind], id)];
        applyFields(kind, id, body);
        return [200, configNode(kind, id)];
      });
    }
    if (method === "DELETE") return answer(deleteNode(kind, id));
  }

  // Links between nodes.
  if (method === "POST" || method === "DELETE") {
    const remove = method === "DELETE";
    const ask = (body, handler) => (remove ? answer(handler({})) : write(handler));
    if ((m = p.match(/^\/config\/projects\/([^/]+)\/program$/))) {
      const projectId = dec(m[1]);
      return ask(null, (body) => {
        const programId = remove ? url.searchParams.get("program_id") : body.program_id;
        const gone = detail(`program link for project ${projectId} was not found`);
        if (remove && !programId) {
          // Without a program id the backend removes every program link the project has.
          const refused = need("project", projectId);
          if (refused) return refused;
          const had = [...dirItem("project", projectId).program_ids];
          had.forEach((id) => links.programProject(id, projectId, true));
          return had.length > 0 ? [204, null] : [404, gone];
        }
        return changeLink(
          ["program", "project"],
          links.programProject,
          [programId, projectId],
          remove,
          undefined,
          gone.detail,
        );
      });
    }
    if ((m = p.match(/^\/config\/pods\/([^/]+)\/projects\/([^/]+)$/))) {
      const [podId, projectId] = [dec(m[1]), dec(m[2])];
      return ask(null, () =>
        changeLink(["project", "pod"], links.projectPod, [projectId, podId], remove),
      );
    }
    if ((m = p.match(/^\/config\/projects\/([^/]+)\/workstreams\/([^/]+)$/))) {
      const ids = [dec(m[1]), dec(m[2])];
      return ask(null, () =>
        changeLink(["project", "workstream"], links.projectWorkstream, ids, remove),
      );
    }
    if ((m = p.match(/^\/config\/pods\/([^/]+)\/workstreams\/([^/]+)$/))) {
      const ids = [dec(m[1]), dec(m[2])];
      return ask(null, () => changeLink(["pod", "workstream"], links.podWorkstream, ids, remove));
    }
    if ((m = p.match(/^\/config\/workstreams\/([^/]+)\/tasks\/([^/]+)$/))) {
      const ids = [dec(m[1]), dec(m[2])];
      return ask(null, () => changeLink(["workstream", "task"], links.workstreamTask, ids, remove));
    }
    if ((m = p.match(/^\/config\/pods\/([^/]+)\/members\/([^/]+)$/))) {
      const ids = [dec(m[1]), dec(m[2])];
      return ask(null, (body) => {
        if (!remove && !body.role) {
          return [
            422,
            { detail: [{ loc: ["body", "role"], msg: "Field required", type: "missing" }] },
          ];
        }
        return changeLink(["pod", "member"], links.podMember, ids, remove, body.role);
      });
    }
  }

  // Escalation contacts.
  if ((m = p.match(/^\/config\/pods\/([^/]+)\/escalation-contacts$/))) {
    const podId = dec(m[1]);
    if (!dirItem("pod", podId)) return answer([404, notFound("pod", podId)]);
    if (method === "GET") {
      const saved = contacts.get(podId) ?? { scrum_master: null, manager: null };
      return answer([200, { pod_id: podId, ...saved }]);
    }
    if (method === "PUT") return write((body) => putContacts(podId, body));
  }
  if ((m = p.match(/^\/config\/pods\/([^/]+)\/escalation-candidates$/)) && method === "GET") {
    const podId = dec(m[1]);
    if (!dirItem("pod", podId)) return answer([404, notFound("pod", podId)]);
    const roleIn = podRoles.get(podId) ?? new Map();
    const candidates = [...members.values()].map((member) => ({
      member_id: member.id,
      name: member.name,
      chat_user_id: chatIdOf(member.id),
      in_pod: roleIn.has(member.id),
      pod_role: roleIn.get(member.id) ?? null,
    }));
    candidates.sort(
      (a, b) => Number(!a.in_pod) - Number(!b.in_pod) || a.name.localeCompare(b.name),
    );
    return answer([200, candidates]);
  }
  return false;
}
