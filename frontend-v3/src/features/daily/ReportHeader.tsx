import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { DayReportResponse } from "../../api/schema";
import { ConfirmDialog, TextDialog } from "../../components/Dialogs";
import { RagChip } from "../../components/ui/RagChip";
import { Pill } from "../../components/ui/Pill";
import { formatDay, formatTime, weekdaysLabel } from "../../lib/format";
import { RUN_LABELS, RUN_TONES, outcomeLine, sendConfirmation, todaysNote } from "./reportView";

/**
 * When the report goes out and to whom, and what this reader may do with it.
 * The server decides `can_send` and `can_write_note` per report, so the buttons
 * follow the project as well as the role.
 */
export function ReportHeader({ report }: { report: DayReportResponse }) {
  const queryClient = useQueryClient();
  const today = new Date().toISOString().slice(0, 10);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["day-reports"] });

  const send = useMutation({
    mutationFn: () => apiClient.sendDayReport(report.report_id),
    onSuccess: (run) => {
      toast.success(`${RUN_LABELS[run.status]}: ${outcomeLine(run)}.`);
      void refresh();
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const note = useMutation({
    mutationFn: (text: string) => apiClient.writeDayReportNote(report.report_id, text),
    onSuccess: () => {
      toast.success("Today's note saved.");
      void refresh();
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const confirm = sendConfirmation(report);
  const { schedule, last_run: lastRun } = report;

  return (
    <section className="flex flex-wrap items-start justify-between gap-4 rounded-3xl bg-grey-fill p-5">
      <div className="min-w-0">
        <h1 className="text-[24px] font-extrabold tracking-tight text-balance">{report.name}</h1>
        <p className="mt-1 text-[13px] text-grey-body">
          {report.enabled ? "On" : "Off"} · {weekdaysLabel(schedule.weekdays)} at{" "}
          {schedule.local_time} {schedule.timezone}
          {report.release_name ? ` · release ${report.release_name}` : ""}
        </p>
        <p className="mt-1 text-[13px] text-grey-body">Goes to {report.audience_summary}.</p>
        {lastRun ? (
          <p className="mt-2 flex flex-wrap items-center gap-2 text-[13px] text-grey-secondary">
            <RagChip tone={RUN_TONES[lastRun.status]} className="h-6 px-2.5 text-[12px]">
              {RUN_LABELS[lastRun.status]}
            </RagChip>
            Last send {formatDay(lastRun.report_date)} {formatTime(lastRun.started_at)} ·{" "}
            {outcomeLine(lastRun)}
          </p>
        ) : (
          <p className="mt-2 text-[13px] text-grey-secondary">Not sent yet.</p>
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        {report.can_send ? (
          <ConfirmDialog
            trigger={
              <Pill size="sm" disabled={send.isPending}>
                {send.isPending ? "Sending…" : "Send now"}
              </Pill>
            }
            title={confirm.title}
            description={confirm.description}
            confirmLabel="Send now"
            onConfirm={() => send.mutate()}
          />
        ) : null}
        {report.can_write_note ? (
          <TextDialog
            trigger={
              <Pill size="sm" variant="ghost">
                {todaysNote(report, today) ? "Edit today's note" : "Write today's note"}
              </Pill>
            }
            title="Today's note"
            description="Opens today's report, signed with your name. It is not carried to tomorrow."
            initial={todaysNote(report, today)}
            saveLabel="Save note"
            saving={note.isPending}
            onSave={(text) => note.mutateAsync(text)}
          />
        ) : null}
        {!report.can_send && !report.can_write_note ? (
          <span className="text-[13px] text-grey-secondary">Read only for your role</span>
        ) : null}
      </div>
    </section>
  );
}
