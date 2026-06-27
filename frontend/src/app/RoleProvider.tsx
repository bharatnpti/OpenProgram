import { useMemo, useState, type ReactNode } from "react";

import {
  readStoredRole,
  roleLabels,
  RoleContext,
  STORAGE_KEY,
  type AppRole,
  type RoleContextValue,
} from "./role";

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
      canAccessPortfolio: role === "mgr" || role === "exec" || role === "admin",
      canAccessAdmin: role === "admin",
    }),
    [role],
  );

  return <RoleContext.Provider value={value}>{children}</RoleContext.Provider>;
}
