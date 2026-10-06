import { createContext, useContext } from "react";

import type { AuthStatusResponse, DevUserResponse } from "../api/schema";
import { appRoles, rolePriority, type AppRole } from "./roleWords";

/*
 * Same roles, storage keys and lens rules as frontend-v2 (src/app/role.ts), so
 * a person acting as someone in one console is the same person in the other.
 */

export { appRoles, roleLabels, rolePriority, type AppRole } from "./roleWords";
export type AuthProvider = "dev" | "oidc_bff";

export const STORAGE_KEY = "openprogram.active-role";
export const USER_STORAGE_KEY = "openprogram.acting-as";

/**
 * The backend could not say who is signed in: it gave no answer, or an error.
 * Not the same as being signed out, so it gets its own screen and a retry.
 */
export type AuthProblem = {
  /** The address that was asked, in full. */
  url: string;
  /** What happened, in a sentence. */
  detail: string;
};

export type RoleContextValue = {
  role: AppRole;
  setRole: (role: AppRole) => void;
  roleLabel: string;
  /** Every role the person holds; the lens is one of these. */
  roles: AppRole[];
  provider: AuthProvider;
  authenticated: boolean;
  authLoading: boolean;
  /** Set when the backend could not be asked who this is; null once it answers. */
  authProblem: AuthProblem | null;
  /** Ask the backend again after `authProblem`; true while that is under way. */
  retryAuth: () => void;
  authRetrying: boolean;
  user: AuthStatusResponse["user"] | null;
  isDevMode: boolean;
  /** Persona switching is served (backend `demo_mode`). */
  demoMode: boolean;
  /**
   * These mirror the backend's capabilities only so the app can say up front
   * which role opens a panel. The backend still decides: every panel also
   * handles a 403 and shows the server's reason.
   */
  /** `READ_PROJECT_PROGRESS`: product owner, manager, executive, admin. */
  canReadProjectProgress: boolean;
  /** `READ_TEAM_AGGREGATE` or `READ_EXEC_AGGREGATE`: everyone but the developer. */
  canReadAggregate: boolean;
  /** `MANAGE_CONFIG`: admin. Reading a project's escalation matrix needs it. */
  canManageConfig: boolean;
  /** `READ_POD_CHECKINS` + `READ_POD_BLOCKERS`: scrum master, manager, admin. */
  canReadPodDetail: boolean;
  /** Program rollups and the portfolio heatmap: manager, executive, admin. */
  canReadPortfolio: boolean;
  /** `SET_UP_DAY_REPORTS`: scrum master (own projects), manager, admin. */
  canSetUpDayReports: boolean;
  /** The built-in chat is served (backend `chat_simulator_enabled`, local tenants). */
  chatEnabled: boolean;
  signIn: () => void;
  logout: () => Promise<void>;
  /** People this app can act as; empty outside a local dev-auth tenant. */
  people: DevUserResponse[];
  peopleLoading: boolean;
  actingAs: DevUserResponse | null;
  setActingAsId: (id: string) => void;
  displayName: string;
};

export const RoleContext = createContext<RoleContextValue | null>(null);

export function readStoredRole(): AppRole {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return appRoles.includes(stored as AppRole) ? (stored as AppRole) : "dev";
  } catch {
    return "dev";
  }
}

export function readStoredUserId(): string | null {
  try {
    return localStorage.getItem(USER_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function store(key: string, value: string | null): void {
  try {
    if (value === null) {
      localStorage.removeItem(key);
    } else {
      localStorage.setItem(key, value);
    }
  } catch {
    // Private windows and blocked storage: the choice simply isn't remembered.
  }
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
