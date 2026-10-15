import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { apiClient } from "../../api/client";
import { useRole } from "../../app/role";
import { PanelState } from "../../components/PanelState";
import { Pill } from "../../components/ui/Pill";
import { ReportHeader } from "../../features/daily/ReportHeader";
import { noDayReportWords } from "../../features/daily/reportView";
import { ReportPreview } from "../../features/daily/ReportPreview";
import { RunHistory } from "../../features/daily/RunHistory";
import { TabIntro } from "../../features/hub/TabIntro";
import { Briefs } from "../../features/reports/Briefs";
import { ReportSetupDialog } from "../../features/reports/ReportSetupDialog";
import { cn } from "../../lib/utils";

/**
 * Project › Reports (`/projects/:projectId/reports`): the project's day
 * reports, then its weekly briefs. A day report is drawn as pictures from the
 * report itself, with the text Send now sends one toggle away. A project can
 * have several (the whole project, or one release of it); `?report=` picks
 * one. Every reader reads the same report: nothing in it is news to anyone the
 * message reaches. An executive reads no day report: the briefs only.
 */
export function ProjectReports({ projectId }: { projectId: string }) {
  const { access } = useRole();
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-8">
      {access.reports.daily ? (
        <DayReport projectId={projectId} />
      ) : (
        <TabIntro tab="reports" meta="This project's briefs, written from the day's facts." />
      )}
      {access.coordination.briefs ? (
        <Briefs
          kind="weekly_project"
          scopeId={projectId}
          title="Briefs for this project"
          empty="No weekly brief for this project yet. They are written from the week's facts on a schedule."
        />
      ) : null}
    </div>
  );
}

function DayReport({ projectId }: { projectId: string }) {
  const [search, setSearch] = useSearchParams();
  const { canSetUpDayReports } = useRole();
  const [showText, setShowText] = useState(false);
  const textId = useId();

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
    <section aria-label="Day report">
      <TabIntro tab="reports" />
      <PanelState
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
          <div className="grid grid-cols-[minmax(0,1fr)] gap-5">
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
                    onClick={() =>
                      setSearch(
                        (current) => {
                          // The viewing day and the release are the hub's; keep them.
                          const next = new URLSearchParams(current);
                          next.set("report", report.report_id);
                          return next;
                        },
                        { replace: true },
                      )
                    }
                  >
                    {report.release_name ?? report.name}
                  </button>
                ))}
              </nav>
            ) : null}
            <div className="grid grid-cols-[minmax(0,1fr)] gap-5">
              <ReportHeader
                report={selected}
                preview={{
                  open: showText,
                  onToggle: () => setShowText((open) => !open),
                  controls: textId,
                }}
              />
              <ReportPreview reportId={selected.report_id} showText={showText} textId={textId} />
              <RunHistory
                key={selected.report_id}
                reportId={selected.report_id}
                timezone={selected.schedule.timezone}
              />
            </div>
          </div>
        ) : null}
      </PanelState>
    </section>
  );
}
