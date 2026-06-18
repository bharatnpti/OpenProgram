import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

export type AppRole = "dev" | "sm" | "po" | "exec" | "admin";

const STORAGE_KEY = "pulseops.active-role";

const roleLabels: Record<AppRole, string> = {
  dev: "Developer",
  sm: "Scrum Master",
  po: "Product Owner",
  exec: "Executive",
  admin: "Admin",
};

type RoleContextValue = {
  role: AppRole;
  setRole: (role: AppRole) => void;
  roleLabel: string;
  canAccessPortfolio: boolean;
  canAccessAdmin: boolean;
};

const RoleContext = createContext<RoleContextValue | null>(null);

function readStoredRole(): AppRole {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (
    stored === "dev" ||
    stored === "sm" ||
    stored === "po" ||
    stored === "exec" ||
    stored === "admin"
  ) {
    return stored;
  }
  return "dev";
}

export function RoleProvider({ children }: { children: ReactNode }) {
  const [role, setRoleState] = useState<AppRole>(readStoredRole);

  const value = useMemo<RoleContextValue>(
    () => ({
      role,
      setRole: (nextRole) => {
        setRoleState(nextRole);
        localStorage.setItem(STORAGE_KEY, nextRole);
      },
      roleLabel: roleLabels[role],
      canAccessPortfolio: role === "exec" || role === "admin",
      canAccessAdmin: role === "admin",
    }),
    [role],
  );

  return <RoleContext.Provider value={value}>{children}</RoleContext.Provider>;
}

export function useRole() {
  const context = useContext(RoleContext);
  if (!context) {
    throw new Error("useRole must be used within RoleProvider");
  }
  return context;
}

export const appRoles: AppRole[] = ["dev", "sm", "po", "exec", "admin"];
