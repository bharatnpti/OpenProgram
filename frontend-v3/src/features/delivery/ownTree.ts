// A person's own part of the delivery tree (`GET /me/delivery-tree`), shaped for
// Delivery's navigator, its default page, the ⌘K palette and the redirect of a
// link outside it. The server says what each node opens on for the caller
// (`access`) and gives a colour only where they read it; this only arranges it.
// Type imports, and runtime imports by their .ts path, so `node --test` runs it.
import type { DeliveryTreeNodeResponse, DeliveryTreeResponse, Rag } from "../../api/schema";
import { ragSeverity } from "../../lib/status.ts";

export type OwnNode = {
  id: string;
  name: string;
  /** Null where the person reads no colour for it: nothing is drawn, not grey. */
  rag: Rag | null;
  /** Its panel opens: every listed project, and a pod with more than its name. */
  opens: boolean;
  /** The whole panel: a project's progress and dates, a pod's check-ins, blockers and tasks. */
  full: boolean;
  /** A pod the person belongs to or runs. */
  own: boolean;
};

export type OwnProject = OwnNode & { programIds: string[]; pods: OwnNode[] };

/** The projects under one program, the program named only as their heading. */
export type OwnGroup = { program: { id: string; name: string } | null; projects: OwnProject[] };

const node = (item: DeliveryTreeNodeResponse, opens: boolean): OwnNode => ({
  id: item.id,
  name: item.name,
  rag: item.access === "name" ? null : (item.rag ?? null),
  opens,
  full: item.access === "panel",
  own: item.own,
});

/** Worst first where a colour is known, then by name; a node with none sorts by name. */
function byColourThenName(a: OwnNode, b: OwnNode): number {
  const known = (n: OwnNode) => (n.rag === null ? -1 : ragSeverity(n.rag));
  return known(b) - known(a) || a.name.localeCompare(b.name);
}

/** The pods that open first (worst first), then the ones listed by name only. */
function podOrder(a: OwnNode, b: OwnNode): number {
  return Number(b.opens) - Number(a.opens) || byColourThenName(a, b);
}

/** Each listed project with its pods, worst first. A project always opens: it is the person's. */
export function ownProjects(tree: DeliveryTreeResponse): OwnProject[] {
  return tree.projects
    .map((project) => ({
      ...node(project, true),
      programIds: project.parent_ids,
      pods: tree.pods
        .filter((pod) => pod.parent_ids.includes(project.id))
        .map((pod) => node(pod, pod.access !== "name"))
        .sort(podOrder),
    }))
    .sort(byColourThenName);
}

/**
 * The navigator: the person's projects under the program each belongs to, the
 * program only a heading (its own panel is not theirs). A project in several
 * programs sits under the first; one in none comes last, under no heading.
 */
export function ownGroups(tree: DeliveryTreeResponse): OwnGroup[] {
  const projects = ownProjects(tree);
  const groups: OwnGroup[] = tree.programs.map((program) => ({
    program: { id: program.id, name: program.name },
    projects: [],
  }));
  const loose: OwnProject[] = [];
  for (const project of projects) {
    const group = groups.find((g) => g.program && project.programIds[0] === g.program.id);
    if (group) group.projects.push(project);
    else loose.push(project);
  }
  const shown = groups.filter((g) => g.projects.length > 0);
  return loose.length > 0 ? [...shown, { program: null, projects: loose }] : shown;
}

/**
 * Where `/delivery` opens: the first project whose whole panel the person
 * reads; else (a developer, whose project is a name) their own pod; else the
 * first project. Null when nothing is listed.
 */
export function ownLanding(tree: DeliveryTreeResponse): string | null {
  const projects = ownProjects(tree);
  const full = projects.find((project) => project.full);
  if (full) return `/delivery/project/${encodeURIComponent(full.id)}`;
  const pod = projects.flatMap((project) => project.pods).find((p) => p.own && p.opens);
  if (pod) return `/delivery/pod/${encodeURIComponent(pod.id)}`;
  return projects[0] ? `/delivery/project/${encodeURIComponent(projects[0].id)}` : null;
}

/** What a redirect needs of the person's part: which projects are listed, and each pod. */
export type OwnLinks = {
  projects: string[];
  /** A listed pod: whether it opens, and its listed projects; null when it is not listed. */
  pod: (podId: string) => { opens: boolean; projectIds: string[] } | null;
};

export function ownLinks(tree: DeliveryTreeResponse): OwnLinks {
  return {
    projects: tree.projects.map((project) => project.id),
    pod: (podId) => {
      const pod = tree.pods.find((item) => item.id === podId);
      return pod ? { opens: pod.access !== "name", projectIds: pod.parent_ids } : null;
    },
  };
}

/** The pods whose whole panel the person reads: the ones a person row may open. */
export function fullPodIds(tree: DeliveryTreeResponse): Set<string> {
  return new Set(tree.pods.filter((pod) => pod.access === "panel").map((pod) => pod.id));
}

/** "Digital Platform Program › Checkout Revamp", the trail above a panel; names only. */
export function trailOf(
  tree: DeliveryTreeResponse,
  kind: "project" | "pod",
  id: string,
): { label: string; to: string | null }[] {
  const projectOf = (projectId: string) => tree.projects.find((p) => p.id === projectId);
  const programOf = (project: DeliveryTreeNodeResponse | undefined) =>
    tree.programs.find((program) => project?.parent_ids[0] === program.id);
  if (kind === "project") {
    const program = programOf(projectOf(id));
    return program ? [{ label: program.name, to: null }] : [];
  }
  const pod = tree.pods.find((item) => item.id === id);
  const project = pod ? projectOf(pod.parent_ids[0]) : undefined;
  if (!project) return [];
  const program = programOf(project);
  return [
    ...(program ? [{ label: program.name, to: null }] : []),
    { label: project.name, to: `/delivery/project/${encodeURIComponent(project.id)}` },
  ];
}
