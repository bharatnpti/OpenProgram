import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiClient } from "../api/client";
import { useProjects } from "../app/directory";
import { useRole } from "../app/role";
import { PanelState, SectionHeader } from "../components/PanelState";
import { RagBadge } from "../components/ui/Bits";
import { ReportSetupDialog } from "../features/reports/ReportSetupDialog";
import { Pill } from "../components/ui/Pill";
import { ragSeverity } from "../lib/status";

/**
 * Every project with its two report views one click away: Daily (the
 * end-of-day report) and Overall (the state since it started). Whoever may
 * set reports up can start one here.
 */
export function ReportsHomePage() {
  const { canSetUpDayReports } = useRole();
  const projects = useProjects();
  const reports = useQuery({
    queryKey: ["day-reports", "all"],
    queryFn: () => apiClient.dayReports(),
  });

  const sorted = [...(projects.data ?? [])].sort(
    (a, b) => ragSeverity(b.rag) - ragSeverity(a.rag) || a.name.localeCompare(b.name),
  );
  const reportsOf = (projectId: string) =>
    (reports.data ?? []).filter((report) => report.project_id === projectId);

  return (
    <>
      <SectionHeader
        title="Reports"
        meta="Each project's end-of-day report and its overall state, worst first."
        actions={
          canSetUpDayReports ? (
            <ReportSetupDialog trigger={<Pill size="sm">Set up a day report</Pill>} />
          ) : undefined
        }
      />
      <PanelState
        needs="anyone with a member record"
        isLoading={projects.isLoading}
        error={projects.error}
        onRetry={() => void projects.refetch()}
        isEmpty={sorted.length === 0}
        emptyText="No projects are configured yet. An admin adds them under Admin → Entities."
      >
        <ul className="grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {sorted.map((project) => {
            const list = reportsOf(project.id);
            const pods = project.pod_ids.length;
            return (
              <li
                key={project.id}
                className="flex min-w-0 flex-col gap-3 rounded-3xl border border-grey-border p-5"
              >
                <div className="flex items-start justify-between gap-3">
                  <span className="text-[17px] font-extrabold">{project.name}</span>
                  <RagBadge rag={project.rag} />
                </div>
                <span className="text-[13px] text-grey-secondary">
                  {list.length === 0
                    ? "No day report set up yet"
                    : list.length === 1
                      ? "1 day report"
                      : `${list.length} day reports`}
                  {pods > 0 ? ` · ${pods} ${pods === 1 ? "pod" : "pods"}` : ""}
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
