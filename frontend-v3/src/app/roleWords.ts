// Roles and how people are named by them. No runtime imports, so `node --test`
// can run it; app/role.ts re-exports the constants for the rest of the app.

export type AppRole = "dev" | "sm" | "po" | "mgr" | "exec" | "admin";

export const appRoles: AppRole[] = ["dev", "sm", "po", "mgr", "exec", "admin"];

export const roleLabels: Record<AppRole, string> = {
  dev: "Developer",
  sm: "Scrum Master",
  po: "Product Owner",
  mgr: "Manager",
  exec: "Executive",
  admin: "Admin",
};

/** Most-privileged first: how a person's default lens is chosen. */
export const rolePriority: AppRole[] = ["admin", "exec", "mgr", "po", "sm", "dev"];

/**
 * The order people are listed in the acting-as picker: who they lead first.
 * Admin comes last because it is a hat someone wears beside their own role.
 */
const GROUP_ORDER: AppRole[] = ["exec", "mgr", "po", "sm", "dev", "admin"];

/** "Manager + Admin", in picker order; "no role" when none is one the console knows. */
export function rolesLabel(roles: readonly string[] | null | undefined): string {
  const known = GROUP_ORDER.filter((role) => (roles ?? []).includes(role));
  return known.map((role) => roleLabels[role]).join(" + ") || "no role";
}

type Person = { name: string; title?: string | null; roles: string[] };

/**
 * People grouped under their most senior role, each person once, in the order
 * they came (the backend sorts by role, then name). People holding no role the
 * console knows come last, under "No role".
 */
export function groupPeople<P extends Person>(people: P[]): { label: string; people: P[] }[] {
  const groups = new Map<AppRole | null, P[]>();
  for (const person of people) {
    const primary = GROUP_ORDER.find((role) => person.roles.includes(role)) ?? null;
    groups.set(primary, [...(groups.get(primary) ?? []), person]);
  }
  return [...GROUP_ORDER, null]
    .filter((role) => groups.has(role))
    .map((role) => ({
      label: role ? roleLabels[role] : "No role",
      people: groups.get(role) ?? [],
    }));
}

/**
 * One option in the picker, under its role's group: "Asha Rao · Engineering
 * Manager (also Admin)". The group names the main role, so only other roles
 * are added.
 */
export function personOption(person: Person): string {
  const roles = GROUP_ORDER.filter((role) => person.roles.includes(role));
  const others = roles.slice(1).map((role) => roleLabels[role]);
  const title = person.title?.trim();
  const base = title ? `${person.name} · ${title}` : person.name;
  return others.length > 0 ? `${base} (also ${others.join(", ")})` : base;
}

/**
 * What the account menu says about a sign-in with no identity provider: the
 * console acts as whoever is picked. It is the sign-in of a developer's
 * machine, not a "demo": a real tenant runs under it too, and said
 * "local demo" under a real person's name.
 */
export const DEV_SIGN_IN = "local sign-in (development)";

/** The line under the name in the account menu: "Manager + Admin · local sign-in (development)". */
export function accountLine(roles: readonly string[] | null | undefined, isDevMode: boolean) {
  const who = rolesLabel(roles);
  return isDevMode ? `${who} · ${DEV_SIGN_IN}` : who;
}
