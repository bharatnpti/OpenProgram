import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiClient } from "../../api/client";
import type { PortfolioProjectDto } from "../../api/schema";
import { useReportProjects } from "../../app/directory";
import { dailyPath, projectPath } from "../../app/links";
import { useRole } from "../../app/role";
import { PanelState } from "../../components/PanelState";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { dayReportCountWords } from "../../features/daily/reportView";
import { TabIntro } from "../../features/hub/TabIntro";
import { usePortfolioIndex } from "../../features/hub/useHub";
import { verdictPill } from "../../features/portfolio/portfolioWords";
import { Briefs } from "../../features/reports/Briefs";
import { RecentActivity } from "../../features/reports/RecentActivity";
import { ReportSetupDialog } from "../../features/reports/ReportSetupDialog";

const WORST_FIRST: Record<string, number> = {
  off_track: 5,
  no_date: 4,
  at_risk: 3,
  not_enough_data: 2,
  on_track: 1,
  done: 0,
};

/**
 * All projects › Reports (`/portfolio/reports`): every project with its day
 * reports one click away, then the briefs, then the last 7 days across the
 * portfolio. Each card carries the project's date verdict as its one chip,
 * from the hub's bulk read of kept rows (never a live forecast per card); the
 * dates themselves are said on the date tab. A developer sees the projects of
 * their own pods (every project when they are in none), with no chip: they
 * read no project's dates. Whoever may set reports up can start one here. An
 * executive reads no day report: the briefs and the 7 days only.
 */
export function PortfolioReports() {
  const { access, canSetUpDayReports } = useRole();
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-8">
      {access.reports.daily ? (
        <DayReports canSetUp={canSetUpDayReports} />
      ) : (
        <TabIntro tab="reports" meta="Briefs written from the day's facts, newest first." />
      )}
      {access.coordination.briefs ? <Briefs kind={access.coordination.defaultBrief} /> : null}
      {/* The aggregate readers': everyone who reads briefs reads what happened. */}
      {access.coordination.briefs ? <RecentActivity /> : null}
    </div>
  );
}

function DayReports({ canSetUp }: { canSetUp: boolean }) {
  // The same list the project picker inside a project's reports offers.
  const shown = useReportProjects();
  const index = usePortfolioIndex();
  const reports = useQuery({
    queryKey: ["day-reports", "all"],
    queryFn: () => apiClient.dayReports(),
  });
  const rows = new Map<string, PortfolioProjectDto>(
    (index.data?.projects ?? []).map((project) => [project.id, project]),
  );
  const weight = (id: string) => {
    const verdict = rows.get(id)?.verdict;
    return verdict ? WORST_FIRST[verdict] : -1;
  };
  const sorted = [...shown.listed].sort(
    (a, b) => weight(b.id) - weight(a.id) || a.name.localeCompare(b.name),
  );
  const reportsOf = (projectId: string) =>
    (reports.data ?? []).filter((report) => report.project_id === projectId);

  return (
    <section aria-label="Day reports">
      <TabIntro
        tab="reports"
        meta={
          shown.own
            ? "The end-of-day report of your pods' projects."
            : "Each project's end-of-day report, the latest dates first."
        }
        actions={
          canSetUp ? (
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
            const row = rows.get(project.id);
            const pill = row ? verdictPill(row) : null;
            return (
              <li
                key={project.id}
                className="flex min-w-0 flex-col gap-3 rounded-3xl border border-grey-border bg-(--op-viz-surface) p-5"
              >
                <div className="flex items-start justify-between gap-3">
                  <span className="min-w-0 text-[17px] font-extrabold break-words">
                    {project.name}
                  </span>
                  {pill ? (
                    <RagChip tone={pill.tone} className="h-6 flex-none px-2.5 text-[12px]">
                      {pill.label}
                    </RagChip>
                  ) : null}
                </div>
                <span className="text-[13px] text-grey-secondary">
                  {dayReportCountWords(reports.data ? list.length : undefined, reports.isError)}
                  {podCount > 0 ? ` · ${podCount} ${podCount === 1 ? "pod" : "pods"}` : ""}
                </span>
                <div className="mt-auto flex flex-wrap gap-2">
                  <Link
                    to={dailyPath(project.id)}
                    className="inline-flex h-9 items-center rounded-full bg-ink px-4 text-[13px] font-bold text-white no-underline hover:bg-ink-hover"
                  >
                    Day reports
                  </Link>
                  <Link
                    to={projectPath(project.id)}
                    className="inline-flex h-9 items-center rounded-full border border-ink px-4 text-[13px] font-bold text-ink no-underline hover:bg-grey-fill"
                  >
                    Project
                  </Link>
                </div>
              </li>
            );
          })}
        </ul>
      </PanelState>
    </section>
  );
}
