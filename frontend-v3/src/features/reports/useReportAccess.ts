import { useMemo } from "react";

import { useRole, type AppRole } from "../../app/role";
import { accessFor, type ReportAccess } from "./access";

/**
 * What the viewing lens may change in Reports. The lens is the one role the API
 * is told under local dev auth, and every held role under real sign-in, as in
 * RoleProvider. Every write control in Reports reads this one hook, so a rule
 * that stops all writes (a past day viewed) has one place to join.
 */
export function useReportAccess(): ReportAccess & { lens: AppRole[] } {
  const { role, roles, isDevMode } = useRole();
  return useMemo(() => {
    const lens = isDevMode ? [role] : roles;
    return { ...accessFor(lens), lens };
  }, [isDevMode, role, roles]);
}
