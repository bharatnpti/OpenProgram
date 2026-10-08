import { useQuery } from "@tanstack/react-query";
import { useParams, useSearchParams } from "react-router-dom";

import { apiClient } from "../api/client";
import { useRole } from "../app/role";
import { PanelState } from "../components/PanelState";
import { Pill } from "../components/ui/Pill";
import { ReportHeader } from "../features/daily/ReportHeader";
import { noDayReportWords } from "../features/daily/reportView";
import { ReportPreview } from "../features/daily/ReportPreview";
import { RunHistory } from "../features/daily/RunHistory";
import { ReportSetupDialog } from "../features/reports/ReportSetupDialog";
import { ReportsHeader } from "../features/reports/ReportsHeader";
import { cn } from "../lib/utils";

/**
 * The end-of-day report as its readers get it. A project can have several (the
 * whole project, or one release of it); `?report=` picks one.
 */
export function DailyPage() {
  const { projectId = "" } = useParams();
  const [search, setSearch] = useSearchParams();
  const { canSetUpDayReports } = useRole();

  const reports = useQuery({
    queryKey: ["day-reports", "project", projectId],
    queryFn: () => apiClient.dayReports(projectId),
    enabled: projectId !== "",
  });
  // Which projects this person may set a report up for: a scrum master's are the
  // projects of the pods they run, so the role alone is not enough to offer it here.
  const setup = useQuery({
    queryKey: ["day-reports", "setup"],
    queryFn: () => apiClient.dayReportSetup(),
    // Only the empty page offers set-up, so only it needs to know where.
    enabled: canSetUpDayReports && reports.isSuccess && (reports.data ?? []).length === 0,
  });
  const mayHere = (setup.data?.projects ?? []).some((project) => project.id === projectId);
  const list = reports.data ?? [];
  const selected = list.find((report) => report.report_id === search.get("report")) ?? list[0];

  return (
    <>
      <ReportsHeader projectId={projectId} />
      <PanelState
        needs="anyone with a member record"
        isLoading={reports.isLoading}
        error={reports.error}
        onRetry={() => void reports.refetch()}
        isEmpty={list.length === 0}
        emptyText={
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span>
              No day report is set up for this project yet.{" "}
              {noDayReportWords(
                canSetUpDayReports,
                setup.isSuccess ? "ready" : setup.isError ? "failed" : "loading",
                mayHere,
              )}
            </span>
            {canSetUpDayReports && mayHere ? (
              <ReportSetupDialog
                projectId={projectId}
                trigger={<Pill size="sm">Set up a day report</Pill>}
              />
            ) : null}
          </div>
        }
      >
        {selected ? (
          <div className="grid grid-cols-[minmax(0,1fr)] gap-6">
            {list.length > 1 ? (
              <nav className="flex flex-wrap gap-2" aria-label="Report">
                {list.map((report) => (
                  <button
                    key={report.report_id}
                    type="button"
                    aria-pressed={report.report_id === selected.report_id}
                    className={cn(
                      "rounded-full border px-4 py-2 text-[13px] font-bold",
                      report.report_id === selected.report_id
                        ? "border-ink bg-ink text-white"
                        : "border-grey-border text-grey-body hover:bg-grey-fill",
                    )}
                    onClick={() => setSearch({ report: report.report_id }, { replace: true })}
                  >
                    {report.release_name ?? report.name}
                  </button>
                ))}
              </nav>
            ) : null}
            <ReportHeader report={selected} />
            <ReportPreview reportId={selected.report_id} />
            <RunHistory reportId={selected.report_id} />
          </div>
        ) : null}
      </PanelState>
    </>
  );
}
