import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { ProjectDayReportResponse } from "../../api/schema";
import { useRole } from "../../app/role";
import { useViewingDate } from "../../app/viewingDate";
import { Card } from "../../components/ui/Card";
import { TextArea } from "../../components/ui/Field";
import { Pill } from "../../components/ui/Pill";
import { errorMessage } from "../admin/adminTypes";
import { scheduleLabel } from "../admin/reportForm";

const MAX_NOTE = 1000;

/**
 * The project's day reports and the note each opens with today.
 *
 * The product owner or a manager writes what the report should say first:
 * the news, or the ask. It goes out with that day's report only.
 */
export function DayReportNoteCard({ projectId }: { projectId: string }) {
  const { isPast } = useViewingDate();
  // The note is for today's report, so it is not offered while a past day is viewed.
  const canSetProjectDates = useRole().canSetProjectDates && !isPast;
  const reports = useQuery({
    queryKey: ["persona", "day-reports", projectId],
    queryFn: () => apiClient.projectDayReports(projectId),
    enabled: canSetProjectDates,
  });
  if (!canSetProjectDates || !reports.data || reports.data.length === 0) return null;
  return (
    <Card padding="p-6" className="flex flex-col gap-4">
      <div>
        <h3 className="text-[18px] font-bold">Today&apos;s report</h3>
        <p className="mt-1 max-w-[620px] text-[13px] text-grey-secondary">
          What the day report should say first: the news, or what you need. It goes out with
          today&apos;s report only.
        </p>
      </div>
      {reports.data.map((report) => (
        <NoteEditor key={report.report_id} projectId={projectId} report={report} />
      ))}
    </Card>
  );
}

function NoteEditor({
  projectId,
  report,
}: {
  projectId: string;
  report: ProjectDayReportResponse;
}) {
  const queryClient = useQueryClient();
  const saved = report.note?.text ?? "";
  const [text, setText] = useState(saved);
  const write = useMutation({
    mutationFn: (next: string) => apiClient.writeDayReportNote(report.report_id, next),
    onSuccess: async (result) => {
      setText(result.note?.text ?? "");
      await queryClient.invalidateQueries({ queryKey: ["persona", "day-reports", projectId] });
      toast.success(result.note ? "Note saved for today's report." : "Note removed.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const id = `report-note-${report.report_id}`;
  return (
    <div className="flex flex-col gap-2 rounded-2xl border border-grey-border p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <label htmlFor={id} className="text-[15px] font-bold">
          {report.name}
        </label>
        <span className="text-[12px] text-grey-secondary">
          {report.enabled
            ? scheduleLabel(
                report.schedule.local_time,
                report.schedule.timezone,
                report.schedule.weekdays,
              )
            : "Switched off"}
          {report.release_id ? " · one release" : ""} · {report.destination_count}{" "}
          {report.destination_count === 1 ? "destination" : "destinations"}
        </span>
      </div>
      <TextArea
        id={id}
        value={text}
        maxLength={MAX_NOTE}
        placeholder="Payments is waiting on the vendor's sandbox; we need an answer by Thursday."
        onChange={(event) => setText(event.target.value)}
      />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-[12px] text-grey-secondary">
          {report.note
            ? `Written by ${report.note.author_name ?? report.note.author}`
            : "No note for today yet."}
        </span>
        <div className="flex gap-2">
          {saved ? (
            <Pill
              variant="ghost"
              size="sm"
              disabled={write.isPending}
              onClick={() => write.mutate("")}
            >
              Remove
            </Pill>
          ) : null}
          <Pill
            size="sm"
            disabled={text.trim() === saved.trim() || !text.trim() || write.isPending}
            onClick={() => write.mutate(text)}
          >
            {write.isPending ? "Saving…" : "Save note"}
          </Pill>
        </div>
      </div>
    </div>
  );
}
