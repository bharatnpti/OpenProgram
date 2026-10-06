// Only type imports here: this module runs under `node --test` as written,
// which strips types but resolves no extensionless runtime import.
import type { DirectoryItemResponse, Rag } from "../api/schema";

/*
 * Whose pods, whose projects, which program: the directory's lists narrowed to
 * what a person works on. Each falls back to everything when it finds nothing
 * (and says so), so a person never opens an empty screen because the seed did
 * not place them.
 */

/** The metadata key a pod keeps its scrum master contact's member id under. */
const POD_SM_KEY = "escalation_sm_member_id";

/**
 * Whether the person runs the pod. The backend's own rule (ForecastService
 * `runs_pod`, which decides who may set a pod's date and send its reports):
 * they are a member of the pod, or the pod names them as its scrum master
 * contact. A scrum master configured only as the contact is in no member list.
 */
export function runsPod(pod: DirectoryItemResponse, memberId: string): boolean {
  return pod.member_ids.includes(memberId) || pod.metadata[POD_SM_KEY] === memberId;
}

/**
 * The pods a person runs or belongs to. `own` is false when they have none and
 * every pod is offered instead, or when nobody is known (a sign-in that names
 * no member), so the screen can say which it is.
 */
export function podsOfPerson(
  pods: DirectoryItemResponse[],
  memberId: string | null | undefined,
): { pods: DirectoryItemResponse[]; own: boolean } {
  if (!memberId) return { pods, own: false };
  const mine = pods.filter((pod) => runsPod(pod, memberId));
  return mine.length > 0 ? { pods: mine, own: true } : { pods, own: false };
}

/** Pods the given member belongs to or runs; every pod when none is known. */
export function podsOf(pods: DirectoryItemResponse[], memberId: string | null | undefined) {
  return podsOfPerson(pods, memberId).pods;
}

function projectsWorkedOnBy(projects: DirectoryItemResponse[], pods: DirectoryItemResponse[]) {
  const ids = new Set(pods.flatMap((pod) => pod.project_ids));
  return projects.filter((project) => ids.has(project.id));
}

/** Projects the given pods work on; every project when the pods name none. */
export function projectsOf(projects: DirectoryItemResponse[], pods: DirectoryItemResponse[]) {
  const mine = projectsWorkedOnBy(projects, pods);
  return mine.length > 0 ? mine : projects;
}

/**
 * The projects of a person's pods. `own` is false when they come from a
 * fallback instead: the pods were not the person's own, or name no project.
 */
export function projectsOfPerson(
  projects: DirectoryItemResponse[],
  pods: DirectoryItemResponse[],
  podsAreOwn: boolean,
): { projects: DirectoryItemResponse[]; own: boolean } {
  const mine = projectsWorkedOnBy(projects, pods);
  return { projects: mine.length > 0 ? mine : projects, own: podsAreOwn && mine.length > 0 };
}

/** The programs the given projects belong to, in the directory's order. */
export function programsOfProjects(
  programs: DirectoryItemResponse[],
  projects: DirectoryItemResponse[],
): DirectoryItemResponse[] {
  const ids = new Set(projects.flatMap((project) => project.program_ids));
  return programs.filter((program) => ids.has(program.id));
}

const SEVERITY: Record<Rag, number> = { red: 3, amber: 2, unknown: 1, green: 0 };

/*
 * Several programs. Every portfolio read hangs off one program root, so a
 * portfolio screen shows one program at a time: the picker lists each with its
 * colour, worst first, and opens on the worst.
 */

/** Worst first, `unknown` above `green` (silence is not a clean bill of health), then by name. */
export function rankPrograms(programs: DirectoryItemResponse[]): DirectoryItemResponse[] {
  return [...programs].sort(
    (a, b) =>
      SEVERITY[b.rag ?? "unknown"] - SEVERITY[a.rag ?? "unknown"] || a.name.localeCompare(b.name),
  );
}

/** The program to show: the one asked for if it exists, else the first of `ranked`. */
export function chooseProgram(
  ranked: DirectoryItemResponse[],
  asked: string | null | undefined,
): DirectoryItemResponse | null {
  return ranked.find((program) => program.id === asked) ?? ranked[0] ?? null;
}

type Scoped = {
  projects: DirectoryItemResponse[];
  workstreams: DirectoryItemResponse[];
  pods: DirectoryItemResponse[];
};

/**
 * What belongs to the chosen program. With one program (or none) the whole
 * directory is the portfolio and nothing is dropped: a project or pod not yet
 * linked to a program stays visible as it always was. With several, each
 * program shows its own projects, and the workstreams and pods of those.
 */
export function scopeToProgram(
  programId: string | null,
  programCount: number,
  all: Scoped,
): Scoped {
  if (!programId || programCount < 2) return all;
  const projects = all.projects.filter((project) => project.program_ids.includes(programId));
  const projectIds = new Set(projects.map((project) => project.id));
  const inProjects = (item: DirectoryItemResponse) =>
    item.project_ids.some((id) => projectIds.has(id));
  return {
    projects,
    workstreams: all.workstreams.filter(inProjects),
    pods: all.pods.filter(inProjects),
  };
}
