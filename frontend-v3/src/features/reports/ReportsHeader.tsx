import { Link, useNavigate } from "react-router-dom";

import { pickerProjects, useReportProjects } from "../../app/directory";
import { dailyPath, projectPath } from "../../app/links";
import { useAssistantSubject } from "../assistant/assistantContext";

/**
 * Inside a project's day report: back to all reports, the project picker, and
 * the project's own page (what was Overall).
 */
export function ReportsHeader({ projectId }: { projectId: string }) {
  const navigate = useNavigate();
  // The projects the Reports home lists for this person; a link to one outside it still opens,
  // and the picker then shows it as the current one.
  const shown = useReportProjects();
  const project = shown.directory.find((item) => item.id === projectId);
  useAssistantSubject(project ? { kind: "project", name: project.name } : null);

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
        onChange={(event) => navigate(dailyPath(event.target.value))}
      >
        {pickerProjects(shown.directory, shown.listed, projectId).map((project) => (
          <option key={project.id} value={project.id}>
            {project.name}
          </option>
        ))}
      </select>
      <Link
        to={projectPath(projectId)}
        className="inline-flex h-10 items-center rounded-full border border-ink px-4 text-[13px] font-bold text-ink no-underline hover:bg-grey-fill"
      >
        Project page
      </Link>
    </div>
  );
}
