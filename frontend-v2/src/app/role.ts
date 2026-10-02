import { createContext, useContext } from "react";

import type { AuthStatusResponse, DevUserResponse } from "../api/schema";

export type AppRole = "dev" | "sm" | "po" | "mgr" | "exec" | "admin";
export type AuthProvider = "dev" | "oidc_bff";

export const STORAGE_KEY = "openprogram.active-role";
export const USER_STORAGE_KEY = "openprogram.acting-as";

export const roleLabels: Record<AppRole, string> = {
  dev: "Developer",
  sm: "Scrum Master",
  po: "Product Owner",
  mgr: "Manager",
  exec: "Executive",
  admin: "Admin",
};

export const appRoles: AppRole[] = ["dev", "sm", "po", "mgr", "exec", "admin"];

/** Most-privileged first: how a person's default lens is chosen. */
export const rolePriority: AppRole[] = ["admin", "exec", "mgr", "po", "sm", "dev"];

export type RoleContextValue = {
  role: AppRole;
  setRole: (role: AppRole) => void;
  roleLabel: string;
  roles: AppRole[];
  provider: AuthProvider;
  authenticated: boolean;
  authLoading: boolean;
  loginUrl: string | null;
  user: AuthStatusResponse["user"] | null;
  isDevMode: boolean;
  /** Persona switching is served (backend `demo_mode`). Off by default. */
  demoMode: boolean;
  /** The built-in chat is served (backend `chat_simulator_enabled`). */
  chatEnabled: boolean;
  canAccessPortfolio: boolean;
  /**
   * Mirrors the backend's aggregate-read guard (`READ_TEAM_AGGREGATE` or
   * `READ_EXEC_AGGREGATE`): admin, exec, manager, product owner, scrum master.
   * A developer has neither, so portfolio-scoped endpoints 403 for them.
   */
  canReadAggregate: boolean;
  /** Mirrors `READ_PROJECT_PROGRESS`: product owner, manager, executive, admin. */
  canReadProjectProgress: boolean;
  /**
   * Mirrors `READ_POD_CHECKINS`/`READ_POD_BLOCKERS`: scrum master, manager,
   * admin. Not the executive: check-ins and blockers are per-person.
   */
  canReadPodDetail: boolean;
  canAccessAdmin: boolean;
  canAccessRole: (role: AppRole) => boolean;
  defaultRoute: string;
  signIn: () => void;
  logout: () => Promise<void>;
  /** People this console can act as; empty outside a local dev-auth tenant. */
  people: DevUserResponse[];
  peopleLoading: boolean;
  /** The person currently being acted as, or null before people load. */
  actingAs: DevUserResponse | null;
  setActingAsId: (id: string) => void;
  /** Display name of whoever the console is acting as. */
  displayName: string;
};

export const RoleContext = createContext<RoleContextValue | null>(null);

export function readStoredRole(): AppRole {
  const stored = localStorage.getItem(STORAGE_KEY);
  return appRoles.includes(stored as AppRole) ? (stored as AppRole) : "dev";
}

export function readStoredUserId(): string | null {
  return localStorage.getItem(USER_STORAGE_KEY);
}

export function isAppRole(value: string): value is AppRole {
  return appRoles.includes(value as AppRole);
}

export function appRolesOf(person: DevUserResponse | null): AppRole[] {
  return (person?.roles ?? []).filter(isAppRole);
}

export function highestRole(roles: AppRole[]): AppRole {
  return rolePriority.find((item) => roles.includes(item)) ?? "dev";
}

export function initialsFor(name: string): string {
  const parts = name.trim().split(/\s+/);
  const initials = parts.slice(0, 2).map((part) => part[0]?.toUpperCase() ?? "");
  return initials.join("") || "OP";
}

export function useRole() {
  const context = useContext(RoleContext);
  if (!context) {
    throw new Error("useRole must be used within RoleProvider");
  }
  return context;
}
