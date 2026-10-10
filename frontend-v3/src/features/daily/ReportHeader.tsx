import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Eye, EyeOff } from "lucide-react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { DayReportResponse } from "../../api/schema";
import { ConfirmDialog, TextDialog } from "../../components/Dialogs";
import { RagChip } from "../../components/ui/RagChip";
import { Pill } from "../../components/ui/Pill";
import { weekdaysLabel } from "../../lib/format";
import { formatInZone } from "../../lib/zones";
import { actionError } from "../reports/access";
import { releaseName } from "../overall/overallWords";
import { ReportSetupDialog } from "../reports/ReportSetupDialog";
import {
  RUN_LABELS,
  RUN_TONES,
  localDay,
  outcomeLine,
  scheduleTime,
  sendConfirmation,
  todaysNote,
} from "./reportView";

/**
 * When the report goes out and to whom, and what this reader may do with it.
 * The server decides `can_send`, `can_edit` and `can_write_note` per report, so
 * the buttons follow the project as well as the role; a button this reader may
 * not use is not there, and no line says who would use it. "Preview what will be
 * sent" is a read, so every reader has it, on a past day too.
 */
export function ReportHeader({
  report,
  preview,
}: {
  report: DayReportResponse;
  /** The sent text beside the report: whether it shows, and the element it is. */
  preview: { open: boolean; onToggle: () => void; controls: string };
}) {
  const queryClient = useQueryClient();
  const today = localDay(report.schedule.timezone);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["day-reports"] });

  const send = useMutation({
    mutationFn: () => apiClient.sendDayReport(report.report_id),
    onSuccess: (run) => {
      toast.success(`${RUN_LABELS[run.status]}: ${outcomeLine(run)}.`);
      void refresh();
    },
    onError: (error) => toast.error(actionError(error)),
  });

  const note = useMutation({
    mutationFn: (text: string) => apiClient.writeDayReportNote(report.report_id, text),
    onSuccess: (_saved, text) => {
      toast.success(text ? "Today's note saved." : "Today's note removed.");
      void refresh();
    },
    onError: (error) => toast.error(actionError(error)),
  });

  const confirm = sendConfirmation(report);
  const { schedule, last_run: lastRun } = report;

  return (
    <section className="flex flex-wrap items-start justify-between gap-x-5 gap-y-3.5 rounded-3xl bg-grey-fill p-4 sm:p-5">
      <div className="min-w-0 flex-[1_1_320px]">
        <h1 className="text-[24px] font-extrabold tracking-tight text-balance">{report.name}</h1>
        <p className="mt-1 text-[13px] text-grey-body">
          {report.enabled ? "On" : "Off"} · {weekdaysLabel(schedule.weekdays)} at{" "}
          {scheduleTime(schedule.local_time)} {schedule.timezone}
          {report.release_name ? ` · ${releaseName(report.release_name)}` : ""} · Goes to{" "}
          {report.audience_summary}
        </p>
        {lastRun ? (
          <p className="mt-2 flex flex-wrap items-center gap-2 text-[13px] text-grey-secondary">
            <RagChip tone={RUN_TONES[lastRun.status]} className="h-6 px-2.5 text-[12px]">
              {RUN_LABELS[lastRun.status]}
            </RagChip>
            Last send {formatInZone(lastRun.started_at, schedule.timezone)} · {outcomeLine(lastRun)}
          </p>
        ) : (
          <p className="mt-2 text-[13px] text-grey-secondary">Not sent yet.</p>
        )}
      </div>

      <div className="max-w-full">
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
              description="Opens today's report, signed with your name. It is not carried to tomorrow. Save it empty to remove it."
              initial={todaysNote(report, today)}
              saveLabel="Save note"
              saving={note.isPending}
              // Resolves only on success, so a refused note keeps the dialog open.
              onSave={(text) => new Promise((done) => note.mutate(text, { onSuccess: done }))}
            />
          ) : null}
          {report.can_edit ? (
            <ReportSetupDialog
              report={report}
              trigger={
                <Pill size="sm" variant="ghost">
                  Change
                </Pill>
              }
            />
          ) : null}
          <Pill
            size="sm"
            variant={preview.open ? "dark" : "ghost"}
            // Pressed, it is filled with ink, which the dark page (.op-viz) turns white:
            // its words take the surface colour, as Overall's numbered markers do.
            className={preview.open ? "text-(color:--op-viz-surface)" : undefined}
            aria-pressed={preview.open}
            aria-controls={preview.open ? preview.controls : undefined}
            onClick={preview.onToggle}
          >
            {preview.open ? (
              <EyeOff aria-hidden className="h-4 w-4" />
            ) : (
              <Eye aria-hidden className="h-4 w-4" />
            )}
            {preview.open ? "Hide the sent text" : "Preview what will be sent"}
          </Pill>
        </div>
      </div>
    </section>
  );
}
