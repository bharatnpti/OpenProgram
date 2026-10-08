import { useMemo } from "react";

import { useRole, type AppRole } from "../../app/role";
import { useReadOnly } from "../../app/viewingDate";
import { accessFor, withoutWrites, type ReportAccess } from "./access";

export type ReportAccessNow = ReportAccess & {
  lens: AppRole[];
  /** What the lens may do on today's data, whatever day is shown. */
  role: ReportAccess;
  /** A past day is shown, so every change here is off. */
  readOnly: boolean;
  /** Why changes are off, in one line; null while they are allowed. */
  reason: string | null;
  /**
   * What a control that is off says: the past day's reason when the role could
   * use it today, else nothing. A control the role never uses is not drawn, and
   * no line says who would use it.
   */
  pastDay: (capability: keyof ReportAccess) => string | null;
};

/**
 * What the viewing lens may change in Reports. The lens is the one role the API
 * is told under local dev auth, and every held role under real sign-in, as in
 * RoleProvider. Every write control in Reports reads this one hook, so a rule
 * that stops all writes has one place to join: while a past day is shown every
 * write flag is false, and `pastDay` says so.
 */
export function useReportAccess(): ReportAccessNow {
  const { role, roles, isDevMode } = useRole();
  const { readOnly, reason } = useReadOnly();
  return useMemo(() => {
    const lens = isDevMode ? [role] : roles;
    const byRole = accessFor(lens);
    return {
      ...(readOnly ? withoutWrites(byRole) : byRole),
      lens,
      role: byRole,
      readOnly,
      reason,
      pastDay: (capability) => (readOnly && reason && byRole[capability] ? reason : null),
    };
  }, [isDevMode, role, roles, readOnly, reason]);
}
