// People & roles, pure: what each console role sees, and a pod's people as the
// team graph lists them. The pages a role is offered come from the same access
// map the sidebar is built from, so the words never name a page the role is
// not offered. Type imports, and runtime imports by their .ts path, so
// `node --test` runs it as written.
import type { GraphTreeDto } from "../../api/schema";
import { accessOf } from "../../app/access.ts";
import { shownNav } from "../../app/nav.ts";
import { appRoles, roleLabels, rolesLabel, type AppRole } from "../../app/roleWords.ts";

/** What each role reads, in one sentence; the server decides each read. */
const SEES: Record<AppRole, string> = {
  dev: "Their own check-in and tasks, their own pod's dates and morning, and their projects' questions, gates and day reports. Teammates' check-ins only as counts.",
  sm: "The check-ins, blockers and nudges of the pods they run or belong to, and the dates, warnings, scope and day reports of those pods' projects.",
  po: "Every project's dates, warnings, scope and reports, changing the dates of their own, and the pods of their own projects with the people in them.",
  mgr: "Every programme, project and pod in full and by name: dates, warnings, heat, check-ins and nudges.",
  exec: "Every project in counts and teams, never a person's name. A pod's own part only for a pod of five people or more.",
  admin:
    "What a manager reads, and Admin: the integrations, the people and pods, the check-ins and who is told when.",
};

export type RoleSees = { role: AppRole; label: string; sees: string; pages: string[] };

/** One row per console role, in the roles' order, with the sidebar pages it is offered. */
export function whatEachRoleSees(): RoleSees[] {
  return appRoles.map((role) => ({
    role,
    label: roleLabels[role],
    sees: SEES[role],
    pages: shownNav(accessOf({ lens: [role], chatEnabled: false })).map((item) => item.label),
  }));
}

export type TeamMember = {
  id: string;
  name: string;
  /** "Backend Engineer · Developer": the job title, then the console roles, each said once. */
  line: string;
};

/** The title and the roles in one line, the roles left out where the title already says them. */
function memberLine(title: string | null, roles: readonly string[]): string {
  const known = roles.length > 0 ? rolesLabel(roles) : null;
  const said = known === "no role" ? null : known;
  if (!title) return said ?? "";
  if (!said || said.toLowerCase() === title.toLowerCase()) return title;
  return `${title} · ${said}`;
}

/**
 * A pod's people from the team graph (`GET /graph/programs/{id}/tree`): each
 * person the pod contains, with their job title and their roles in the console,
 * by name. Null when no tree holds the pod. No time zone and no check-in time:
 * those are an admin's and the pod's own scrum master's.
 */
export function podTeam(
  trees: readonly (GraphTreeDto | undefined)[],
  podId: string,
): TeamMember[] | null {
  for (const tree of trees) {
    if (!tree || !tree.nodes.some((node) => node.id === podId && node.kind === "pod")) continue;
    const people = new Map(
      tree.nodes.filter((node) => node.kind === "developer").map((node) => [node.id, node]),
    );
    const members = new Map<string, TeamMember>();
    for (const edge of tree.edges) {
      if (edge.kind !== "contains" || edge.from_node_id !== podId) continue;
      const person = people.get(edge.to_node_id);
      if (!person || members.has(person.id)) continue;
      const title = person.metadata.title;
      const roles = person.metadata.app_roles;
      members.set(person.id, {
        id: person.id,
        name: person.name,
        line: memberLine(
          typeof title === "string" && title.trim() ? title.trim() : null,
          typeof roles === "string" ? roles.split(",").map((role) => role.trim()) : [],
        ),
      });
    }
    return [...members.values()].sort((a, b) => a.name.localeCompare(b.name));
  }
  return null;
}
