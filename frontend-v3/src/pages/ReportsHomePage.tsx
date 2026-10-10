import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiClient } from "../api/client";
import { useReportProjects } from "../app/directory";
import { useRole } from "../app/role";
import { PanelState, SectionHeader } from "../components/PanelState";
import { dayReportCountWords } from "../features/daily/reportView";
import { ProjectCompactStrip } from "../features/delivery/DeliveryStrips";
import { ProjectStatus } from "../features/reports/ProjectStatus";
import { ReportSetupDialog } from "../features/reports/ReportSetupDialog";
import { Pill } from "../components/ui/Pill";
import { ragSeverity } from "../lib/status";

/**
 * Every project with its two report views one click away: Daily (the
 * end-of-day report) and Overall (the state since it started), and, for whoever
 * reads a project's progress, its dates on one line. A developer sees the
 * projects of their own pods (every project when they are in none). Whoever may
 * set reports up can start one here.
 */
export function ReportsHomePage() {
  const { canSetUpDayReports, canReadProjectProgress } = useRole();
  // The same list the project picker inside a project's reports offers.
  const shown = useReportProjects();
  const reports = useQuery({
    queryKey: ["day-reports", "all"],
    queryFn: () => apiClient.dayReports(),
  });

  const sorted = [...shown.listed].sort(
    (a, b) => ragSeverity(b.rag) - ragSeverity(a.rag) || a.name.localeCompare(b.name),
  );
  const reportsOf = (projectId: string) =>
    (reports.data ?? []).filter((report) => report.project_id === projectId);

  return (
    <>
      <SectionHeader
        title="Reports"
        meta={
          shown.own
            ? "The end-of-day report and overall state of your pods' projects, worst first."
            : "Each project's end-of-day report and its overall state, worst first."
        }
        actions={
          canSetUpDayReports ? (
            <ReportSetupDialog trigger={<Pill size="sm">Set up a day report</Pill>} />
          ) : undefined
        }
      />
      <PanelState
        isLoading={shown.isLoading}
        error={shown.error}
        onRetry={shown.retry}
        isEmpty={sorted.length === 0}
        emptyText="No projects are configured yet. An admin adds them under Admin → Entities."
      >
        <ul className="grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {sorted.map((project) => {
            const list = reportsOf(project.id);
            const podCount = project.pod_ids.length;
            return (
              <li
                key={project.id}
                className="flex min-w-0 flex-col gap-3 rounded-3xl border border-grey-border p-5"
              >
                <div className="flex items-start justify-between gap-3">
                  <span className="text-[17px] font-extrabold">{project.name}</span>
                  <ProjectStatus project={project} />
                </div>
                {canReadProjectProgress ? (
                  <ProjectCompactStrip projectId={project.id} className="" verdict={false} />
                ) : null}
                <span className="text-[13px] text-grey-secondary">
                  {dayReportCountWords(reports.data ? list.length : undefined, reports.isError)}
                  {podCount > 0 ? ` · ${podCount} ${podCount === 1 ? "pod" : "pods"}` : ""}
                </span>
                <div className="mt-auto flex flex-wrap gap-2">
                  <Link
                    to={`/reports/${project.id}/daily`}
                    className="inline-flex h-9 items-center rounded-full bg-ink px-4 text-[13px] font-bold text-white no-underline hover:bg-ink-hover"
                  >
                    Daily
                  </Link>
                  <Link
                    to={`/reports/${project.id}/overall`}
                    className="inline-flex h-9 items-center rounded-full border border-ink px-4 text-[13px] font-bold text-ink no-underline hover:bg-grey-fill"
                  >
                    Overall
                  </Link>
                </div>
              </li>
            );
          })}
        </ul>
      </PanelState>
    </>
  );
}
