// What hangs off what, and what a change to it affects. Pure helpers, type imports only so
// `node --test` can run them. The wording says what the backend really does: deleting a node
// removes its links but never the nodes it was linked to.
import type { ConfigNodeResponse, DirectoryItemResponse } from "../../api/schema";

export type EntityKind = "program" | "project" | "pod" | "workstream" | "member";

/** The kinds an admin can add, change and delete here; members come from the chat directory. */
export type EditableKind = Exclude<EntityKind, "member">;

export const ENTITY_WORDS: Record<EntityKind, { one: string; many: string }> = {
  program: { one: "program", many: "programs" },
  project: { one: "project", many: "projects" },
  pod: { one: "pod", many: "pods" },
  workstream: { one: "workstream", many: "workstreams" },
  member: { one: "member", many: "members" },
};

/** The repositories listed on a node; the response leaves the list out when there are none. */
export const reposOf = (node: Pick<ConfigNodeResponse, "github_repos">): string[] =>
  node.github_repos ?? [];

/** "1 project", "3 projects", "2 people". */
export function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}

/** The singular or the plural wording, so "1 pod stays" and "2 pods stay" both read right. */
const agree = (count: number, singular: string, many: string): string =>
  count === 1 ? singular : many;

/** "A", "A and B", "A, B and C", "A, B, C and 2 more": long lists never run on. */
export function listNames(names: readonly string[], show = 3): string {
  if (names.length === 0) return "";
  if (names.length === 1) return names[0];
  if (names.length <= show)
    return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  return `${names.slice(0, show).join(", ")} and ${names.length - show} more`;
}

/** Who is linked to whom, by id. Both ends of each link are read, so a one-sided view still finds it. */
export type Links = {
  programProjects: Record<string, string[]>;
  projectPods: Record<string, string[]>;
  projectWorkstreams: Record<string, string[]>;
  podWorkstreams: Record<string, string[]>;
  podMembers: Record<string, string[]>;
  workstreamTasks: Record<string, string[]>;
};

const union = (...lists: (readonly string[] | undefined)[]): string[] => [
  ...new Set(lists.flatMap((list) => list ?? [])),
];

/**
 * The links in the directory. `workstreams` must hold every workstream, in use or not: the
 * directory's lists leave an empty one out, so a workstream no task is in is read on its own.
 */
export function deriveLinks(directory: {
  programs: DirectoryItemResponse[];
  projects: DirectoryItemResponse[];
  pods: DirectoryItemResponse[];
  workstreams: DirectoryItemResponse[];
}): Links {
  const { programs, projects, pods, workstreams } = directory;
  const links: Links = {
    programProjects: {},
    projectPods: {},
    projectWorkstreams: {},
    podWorkstreams: {},
    podMembers: {},
    workstreamTasks: {},
  };
  for (const program of programs) {
    links.programProjects[program.id] = union(
      program.project_ids,
      projects.filter((project) => project.program_ids.includes(program.id)).map((p) => p.id),
    );
  }
  for (const project of projects) {
    links.projectPods[project.id] = union(
      project.pod_ids,
      pods.filter((pod) => pod.project_ids.includes(project.id)).map((p) => p.id),
    );
    links.projectWorkstreams[project.id] = union(
      project.workstream_ids,
      workstreams.filter((ws) => ws.project_ids.includes(project.id)).map((w) => w.id),
    );
  }
  for (const pod of pods) {
    links.podWorkstreams[pod.id] = union(
      pod.workstream_ids,
      workstreams.filter((ws) => ws.pod_ids.includes(pod.id)).map((w) => w.id),
    );
    links.podMembers[pod.id] = union(pod.member_ids);
  }
  for (const workstream of workstreams) {
    links.workstreamTasks[workstream.id] = union(workstream.task_ids);
  }
  return links;
}

/** The parents that hold `childId`: the pods a person is in, the projects a pod works on. */
export function parentsOf(map: Record<string, string[]>, childId: string): string[] {
  return Object.entries(map)
    .filter(([, children]) => children.includes(childId))
    .map(([parent]) => parent);
}

/** A record without one key, to read what the links or repositories would be once a node is gone. */
export function withoutKey<T>(record: Record<string, T>, key: string): Record<string, T> {
  return Object.fromEntries(Object.entries(record).filter(([id]) => id !== key));
}

/** The links with one parent-to-child link taken out, to read what a change would leave. */
export function withoutLink(
  map: Record<string, string[]>,
  parentId: string,
  childId: string,
): Record<string, string[]> {
  return { ...map, [parentId]: (map[parentId] ?? []).filter((id) => id !== childId) };
}

// ---- Roles in a pod ---------------------------------------------------------------------------

/** The roles a person can have in a pod. The role is a label; it orders the escalation pickers. */
export const POD_ROLES: { value: string; label: string }[] = [
  { value: "developer", label: "Developer" },
  { value: "scrum_master", label: "Scrum master" },
  { value: "product_owner", label: "Product owner" },
  { value: "manager", label: "Manager" },
];

/** "scrum_master" as "Scrum master"; a role nobody listed is still readable. */
export function podRoleLabel(role: string | null | undefined): string {
  if (!role) return "";
  const known = POD_ROLES.find((item) => item.value === role);
  if (known) return known.label;
  const words = role.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** The pod role that matches what the person does in the console, from their `app_roles`. */
export function suggestPodRole(appRoles: unknown): string {
  const held = typeof appRoles === "string" ? appRoles.split(",").map((r) => r.trim()) : [];
  if (held.includes("sm")) return "scrum_master";
  if (held.includes("po")) return "product_owner";
  if (held.includes("mgr")) return "manager";
  return "developer";
}

// ---- Git repositories: a pod may only read what its projects provide -----------------------------

export type RepoScope = {
  links: Links;
  /** Repositories listed on each project, by id. */
  projectRepos: Record<string, string[]>;
  /** Repositories listed on each pod, by id. */
  podRepos: Record<string, string[]>;
};

/** The repositories a pod may list: the union of those of the projects it works on. */
export function podRepoScope(podId: string, scope: RepoScope): string[] {
  return union(
    ...parentsOf(scope.links.projectPods, podId).map((projectId) => scope.projectRepos[projectId]),
  );
}

/**
 * Pods listing repositories none of their projects provides. The Git sync refuses to build its
 * targets while one exists ("references GitHub repos outside linked project scope"), so the
 * Data sources screen shows a configuration error until it is fixed.
 */
export function podsOutsideScope(scope: RepoScope): { podId: string; repos: string[] }[] {
  const result: { podId: string; repos: string[] }[] = [];
  for (const [podId, repos] of Object.entries(scope.podRepos)) {
    if (repos.length === 0) continue;
    const allowed = new Set(podRepoScope(podId, scope));
    const outside = repos.filter((repo) => !allowed.has(repo));
    if (outside.length > 0) result.push({ podId, repos: outside });
  }
  return result;
}

/** The pods a change would newly put outside their scope, not ones that already were. */
export function newlyOutsideScope(
  before: RepoScope,
  after: RepoScope,
): { podId: string; repos: string[] }[] {
  const already = new Map(podsOutsideScope(before).map((item) => [item.podId, item.repos]));
  return podsOutsideScope(after)
    .map((item) => ({
      podId: item.podId,
      repos: item.repos.filter((repo) => !(already.get(item.podId) ?? []).includes(repo)),
    }))
    .filter((item) => item.repos.length > 0);
}

// ---- What a change affects --------------------------------------------------------------------------

export type LinkKind =
  | "program-project"
  | "project-pod"
  | "project-workstream"
  | "pod-workstream"
  | "pod-member"
  | "member-task"
  | "workstream-task";

/**
 * What taking one link away does. `parent` and `child` are the names at each end:
 * the program and its project, the pod and its person, the person and their task.
 */
export function unlinkWords(
  kind: LinkKind,
  parent: string,
  child: string,
): { title: string; effect: string } {
  switch (kind) {
    case "program-project":
      return {
        title: `Take ${child} out of ${parent}?`,
        effect: `${child} stays as a project, but it leaves ${parent}'s status and the portfolio views until it joins a program again.`,
      };
    case "project-pod":
      return {
        title: `Take ${child} off ${parent}?`,
        effect: `${child} stays as a pod, but its check-ins, blockers and tasks no longer count toward ${parent}'s status.`,
      };
    case "project-workstream":
      return {
        title: `Take ${child} out of ${parent}?`,
        effect: `${child} and its tasks stay, but no longer roll up into ${parent}.`,
      };
    case "pod-workstream":
      return {
        title: `Stop ${parent} working on ${child}?`,
        effect: `Work in ${child} is no longer attributed to ${parent}. Nothing is deleted.`,
      };
    case "pod-member":
      return {
        title: `Take ${child} out of ${parent}?`,
        effect: `${child}'s check-in stops counting toward ${parent}, and if they miss one, ${parent}'s scrum master and manager are no longer told. With no other pod, they show as in no team.`,
      };
    case "member-task":
      return {
        title: `Take ${child} off ${parent}?`,
        effect: `${child} stops showing under ${parent}'s tasks. If Jira still lists them as the assignee, the next Jira sync assigns it again.`,
      };
    case "workstream-task":
      return {
        title: `Take ${child} out of ${parent}?`,
        effect: `${child} stays where it is, but ${parent} no longer groups it. A workstream with no task in it is hidden from Delivery and Today.`,
      };
  }
}

/** The sentence that says a link was made or taken away, once it was. */
export function linkDone(kind: LinkKind, parent: string, child: string, linked: boolean): string {
  if (kind === "pod-workstream") {
    return linked ? `${parent} now works on ${child}.` : `${parent} no longer works on ${child}.`;
  }
  const into: Record<Exclude<LinkKind, "pod-workstream">, [string, string]> = {
    "program-project": ["is now in", "is no longer in"],
    "project-pod": ["is now on", "is no longer on"],
    "project-workstream": ["is now in", "is no longer in"],
    "pod-member": ["is now in", "is no longer in"],
    "member-task": ["is now assigned to", "is no longer assigned to"],
    "workstream-task": ["is now in", "is no longer in"],
  };
  return `${child} ${into[kind][linked ? 0 : 1]} ${parent}.`;
}

export type DeleteImpact = {
  /** What else changes, in plain words. */
  lines: string[];
  /** Consequences to take seriously: shown apart, but the person may still go ahead. */
  warnings: string[];
};

/**
 * What deleting a node changes. The backend removes the node and every link to it; the nodes it
 * was linked to stay. Anything saved about the node under its id stays too, with nothing to show it.
 */
export function deleteImpact(
  kind: EditableKind,
  node: ConfigNodeResponse,
  links: Links,
  name: (id: string) => string,
  extra: { dayReports?: number; outsideScope?: { podId: string; repos: string[] }[] } = {},
): DeleteImpact {
  const lines: string[] = [];
  const warnings: string[] = [];
  const names = (ids: readonly string[]) => listNames(ids.map(name));

  if (kind === "program") {
    const projects = links.programProjects[node.id] ?? [];
    if (projects.length > 0) {
      const n = projects.length;
      lines.push(
        `${plural(n, "project")} (${names(projects)}) ${agree(
          n,
          "stays, but it is no longer in a program, so it drops out of the portfolio views until it joins one again.",
          "stay, but they are no longer in a program, so they drop out of the portfolio views until they join one again.",
        )}`,
      );
    }
  }

  if (kind === "project") {
    const programs = parentsOf(links.programProjects, node.id);
    const pods = links.projectPods[node.id] ?? [];
    const workstreams = links.projectWorkstreams[node.id] ?? [];
    if (programs.length > 0) {
      lines.push(`It leaves ${names(programs)}, so it drops out of that program's status.`);
    }
    if (pods.length > 0) {
      lines.push(
        `${plural(pods.length, "pod")} (${names(pods)}) ${agree(
          pods.length,
          "stays, but no longer rolls up into it.",
          "stay, but no longer roll up into it.",
        )}`,
      );
    }
    if (workstreams.length > 0) {
      lines.push(
        `${plural(workstreams.length, "workstream")} (${names(workstreams)}) ${agree(
          workstreams.length,
          "stays, but is no longer in a project.",
          "stay, but are no longer in a project.",
        )}`,
      );
    }
    const reports = extra.dayReports ?? 0;
    if (reports > 0) {
      lines.push(
        `${plural(reports, "day report")} set up for it ${agree(
          reports,
          "stays in Reports, but every send fails because the project is gone. Remove it first if you don't want that.",
          "stay in Reports, but every send fails because the project is gone. Remove them first if you don't want that.",
        )}`,
      );
    }
    if (node.jira_project_key || reposOf(node).length > 0) {
      lines.push("Its Jira key and repositories go with it, so the syncs stop reading them.");
    }
  }

  if (kind === "pod") {
    const projects = parentsOf(links.projectPods, node.id);
    const workstreams = links.podWorkstreams[node.id] ?? [];
    const members = links.podMembers[node.id] ?? [];
    if (projects.length > 0) {
      lines.push(
        `It leaves ${names(projects)}, so its work no longer counts toward ${agree(projects.length, "it", "them")}.`,
      );
    }
    if (workstreams.length > 0) {
      lines.push(
        `It stops working on ${plural(workstreams.length, "workstream")} (${names(workstreams)}).`,
      );
    }
    if (members.length > 0) {
      lines.push(
        `${plural(members.length, "person", "people")} (${names(members)}) ${agree(
          members.length,
          "stays as a member, but their check-in stops",
          "stay as members, but their check-ins stop",
        )} rolling up into this pod. Anyone in no other pod shows as in no team.`,
      );
    }
    lines.push("Its escalation contacts, if any were set, are removed with it.");
    if (node.jira_filter_jql || reposOf(node).length > 0) {
      lines.push("Syncs scoped to this pod stop; its project's own syncs carry on.");
    }
  }

  if (kind === "workstream") {
    const projects = parentsOf(links.projectWorkstreams, node.id);
    const pods = parentsOf(links.podWorkstreams, node.id);
    const tasks = links.workstreamTasks[node.id] ?? [];
    if (projects.length > 0) lines.push(`It leaves ${names(projects)}.`);
    if (pods.length > 0) {
      lines.push(
        `${plural(pods.length, "pod")} (${names(pods)}) ${agree(pods.length, "stops", "stop")} working on it.`,
      );
    }
    if (tasks.length > 0) {
      lines.push(
        `${plural(tasks.length, "task")} ${agree(
          tasks.length,
          "stays, but is no longer grouped under it.",
          "stay, but are no longer grouped under it.",
        )}`,
      );
    }
  }

  if (lines.length === 0) lines.push("Nothing is linked to it.");
  lines.push("Nothing else is deleted: the things it was linked to, and their history, stay.");

  for (const item of extra.outsideScope ?? []) {
    warnings.push(scopeWarning(name(item.podId), item.repos));
  }
  return { lines, warnings };
}

/** The Git sync refuses a pod that lists a repository none of its projects provides. */
export function scopeWarning(podName: string, repos: readonly string[]): string {
  return `${podName} lists ${listNames(repos)}, which none of its other projects provide. The Git sync reports a configuration error until you clear ${
    repos.length === 1 ? "it" : "them"
  } from the pod.`;
}
