import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";

import { useProjects } from "../../app/directory";
import { cn } from "../../lib/utils";

/** Inside a project's reports: back to all reports, the project picker, and Daily | Overall. */
export function ReportsHeader({ projectId }: { projectId: string }) {
  const navigate = useNavigate();
  const location = useLocation();
  const projects = useProjects();
  const view = location.pathname.endsWith("/overall") ? "overall" : "daily";

  const tab = (to: string, label: string) => (
    <NavLink
      to={to}
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
    <div className="mb-6 flex flex-wrap items-center gap-3">
      <Link
        to="/reports"
        className="text-[13px] font-bold max-sm:inline-flex max-sm:min-h-11 max-sm:items-center"
      >
        ← All reports
      </Link>
      <label htmlFor="project-picker" className="sr-only">
        Project
      </label>
      <select
        id="project-picker"
        className="h-10 max-w-[260px] rounded-full border border-grey-border bg-white px-4 text-[14px] font-bold"
        value={projectId}
        onChange={(event) => navigate(`/reports/${event.target.value}/${view}`)}
      >
        {(projects.data ?? [{ id: projectId, name: projectId }]).map((project) => (
          <option key={project.id} value={project.id}>
            {project.name}
          </option>
        ))}
      </select>
      <nav className="flex gap-1 rounded-full bg-grey-fill p-1" aria-label="Report view">
        {tab(`/reports/${projectId}/daily`, "Daily")}
        {tab(`/reports/${projectId}/overall`, "Overall")}
      </nav>
    </div>
  );
}
