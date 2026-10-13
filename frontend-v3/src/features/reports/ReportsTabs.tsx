import { NavLink } from "react-router-dom";

import { useRole } from "../../app/role";
import { cn } from "../../lib/utils";

/**
 * Reports' two tabs: Daily (the day reports) and Briefs, for the roles that
 * read briefs. Overall is no longer here: it is the Project page. A role with
 * one tab (a developer's day reports, an executive's briefs, D103) gets no tab row.
 */
export function ReportsTabs() {
  const { coordination, reports } = useRole().access;
  if (!coordination.briefs || !reports.daily) return null;
  const tab = (to: string, label: string) => (
    <NavLink
      to={to}
      end
      className={({ isActive }) =>
        cn(
          "rounded-full px-4 py-2 text-[14px] font-bold no-underline",
          isActive ? "bg-ink text-white" : "text-grey-body hover:bg-white",
        )
      }
    >
      {label}
    </NavLink>
  );
  return (
    <nav className="mb-6 flex w-fit gap-1 rounded-full bg-grey-fill p-1" aria-label="Reports">
      {tab("/reports", "Daily")}
      {tab("/reports/briefs", "Briefs")}
    </nav>
  );
}
