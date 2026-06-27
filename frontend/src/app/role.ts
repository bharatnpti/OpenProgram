import { createContext, useContext } from "react";

export type AppRole = "dev" | "sm" | "po" | "mgr" | "exec" | "admin";

export const STORAGE_KEY = "pulseops.active-role";

export const roleLabels: Record<AppRole, string> = {
  dev: "Developer",
  sm: "Scrum Master",
  po: "Product Owner",
  mgr: "Manager",
  exec: "Executive",
  admin: "Admin",
};

export const appRoles: AppRole[] = ["dev", "sm", "po", "mgr", "exec", "admin"];

export type RoleContextValue = {
  role: AppRole;
  setRole: (role: AppRole) => void;
  roleLabel: string;
  canAccessPortfolio: boolean;
  canAccessAdmin: boolean;
};

export const RoleContext = createContext<RoleContextValue | null>(null);

export function readStoredRole(): AppRole {
  const stored = localStorage.getItem(STORAGE_KEY);
  return appRoles.includes(stored as AppRole) ? (stored as AppRole) : "dev";
}

export function useRole() {
  const context = useContext(RoleContext);
  if (!context) {
    throw new Error("useRole must be used within RoleProvider");
  }
  return context;
}
