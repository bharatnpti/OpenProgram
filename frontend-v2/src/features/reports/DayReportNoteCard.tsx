import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight } from "lucide-react";
import { Link } from "react-router-dom";

import { apiClient } from "../../api/client";
import { useRole } from "../../app/role";
import { useViewingDate } from "../../app/viewingDate";
import { Card } from "../../components/ui/Card";
import { ReportNoteEditor } from "./ReportNoteEditor";
import { scheduleLabel } from "./reportForm";

function projectReportsQueryKey(projectId: string) {
  return ["day-reports", "project", projectId] as const;
}

/**
 * The project's day reports and the note each opens with today, for whoever
 * writes that note: the product owner, a manager or an admin. Each links to
 * the report itself on the Reports page.
 */
export function DayReportNoteCard({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const { isPast } = useViewingDate();
  // The note is for today's report, so it is not offered while a past day is viewed.
  const canSetProjectDates = useRole().canSetProjectDates && !isPast;
  const reports = useQuery({
    queryKey: projectReportsQueryKey(projectId),
    queryFn: () => apiClient.dayReports(projectId),
    enabled: canSetProjectDates,
  });
  const writable = (reports.data ?? []).filter((report) => report.can_write_note);
  if (!canSetProjectDates || writable.length === 0) return null;
  return (
    <Card padding="p-6" className="flex flex-col gap-4">
      <div>
        <h3 className="text-[18px] font-bold">Today&apos;s report</h3>
        <p className="mt-1 max-w-[620px] text-[13px] text-grey-secondary">
          What the day report should say first: the news, or what you need. It goes out with
          today&apos;s report only.
        </p>
      </div>
      {writable.map((report) => (
        <div
          key={report.report_id}
          className="flex flex-col gap-2 rounded-2xl border border-grey-border p-4"
        >
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <Link
              to={`/reports?report=${encodeURIComponent(report.report_id)}`}
              className="inline-flex items-center gap-1 text-[15px] font-bold text-ink hover:text-magenta"
            >
              {report.name}
              <ArrowUpRight size={14} aria-hidden />
            </Link>
            <span className="text-[12px] text-grey-secondary">
              {report.enabled
                ? scheduleLabel(
                    report.schedule.local_time,
                    report.schedule.timezone,
                    report.schedule.weekdays,
                  )
                : "Switched off"}
              {report.release_id ? " · one release" : ""} · {report.audience_summary}
            </span>
          </div>
          <ReportNoteEditor
            key={report.note?.updated_at ?? "none"}
            report={report}
            onSaved={() => queryClient.invalidateQueries({ queryKey: ["day-reports"] })}
          />
        </div>
      ))}
    </Card>
  );
}
