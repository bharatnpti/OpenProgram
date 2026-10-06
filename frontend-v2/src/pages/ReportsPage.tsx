import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Send, Trash2 } from "lucide-react";
import { useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { apiClient } from "../api/client";
import type { DayReportResponse, DayReportSetupResponse } from "../api/schema";
import { useRole } from "../app/role";
import { Card } from "../components/ui/Card";
import { Pill } from "../components/ui/Pill";
import { RagChip } from "../components/ui/RagChip";
import { cn } from "../lib/utils";
import { ConfirmDialog } from "../features/admin/ConfirmDialog";
import { errorMessage } from "../features/admin/adminTypes";
import { ReportDialog } from "../features/reports/ReportDialog";
import { ReportHistory } from "../features/reports/ReportHistory";
import { ReportNoteEditor } from "../features/reports/ReportNoteEditor";
import { ReportPreview } from "../features/reports/ReportPreview";
import { scheduleLabel } from "../features/reports/reportForm";
import {
  RUN_LABELS,
  RUN_TONES,
  formatReportDay,
  peopleLine,
  reportRunsQueryKey,
  sendConfirmation,
} from "../features/reports/reportView";

const REPORTS_KEY = ["day-reports", "list"] as const;
const SETUP_KEY = ["day-reports", "setup"] as const;
const REPORT_PARAM = "report";

/**
 * Day reports, for everyone: each project's day as its readers get it.
 *
 * Everyone reads the list, today's report (built now, never sent from here
 * unless asked) and past sends. Sending now and setting reports up are for
 * the scrum master of a pod on the project, a manager or an admin, so each
 * report's own flags decide its buttons. The page always shows today: the
 * report is built live and the set-up is current, so it does not follow a
 * past viewing date.
 */
export function ReportsPage() {
  const { canSetUpDayReports } = useRole();
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const detailRef = useRef<HTMLDivElement>(null);
  const reports = useQuery({ queryKey: REPORTS_KEY, queryFn: () => apiClient.dayReports() });
  const setup = useQuery({
    queryKey: SETUP_KEY,
    queryFn: apiClient.dayReportSetup,
    enabled: canSetUpDayReports,
  });
  const [editing, setEditing] = useState<DayReportResponse | "new" | null>(null);
  const [removing, setRemoving] = useState<DayReportResponse | null>(null);

  const list = reports.data ?? [];
  const wanted = params.get(REPORT_PARAM);
  const selected = list.find((item) => item.report_id === wanted) ?? list[0] ?? null;
  const canCreate = canSetUpDayReports && (setup.data?.projects.length ?? 0) > 0;

  const select = (reportId: string | null, scroll = false) => {
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        if (reportId) next.set(REPORT_PARAM, reportId);
        else next.delete(REPORT_PARAM);
        return next;
      },
      { replace: true },
    );
    // On a narrow screen the report sits below the list, so bring it into view.
    if (scroll && window.matchMedia("(max-width: 1023px)").matches) {
      window.requestAnimationFrame(() =>
        detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
      );
    }
  };
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["day-reports"] });

  const remove = useMutation({
    mutationFn: (reportId: string) => apiClient.removeDayReport(reportId),
    onSuccess: async () => {
      select(null);
      await refresh();
      toast.success("Report removed.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-[28px] font-extrabold">Reports</h1>
          <p className="mt-1 max-w-[720px] text-[15px] text-grey-secondary">
            Each project&apos;s day, read like a status mail: in short, where it stands, what
            matters most, what is needed from whom, and the open questions. This page always shows
            today: the report below is built now, and nothing is sent from it unless someone presses
            Send now.
          </p>
        </div>
        {canCreate ? (
          <Pill size="sm" onClick={() => setEditing("new")}>
            <Plus size={14} />
            New report
          </Pill>
        ) : null}
      </div>

      {reports.isError ? (
        <p className="text-[14px] text-rag-red">{errorMessage(reports.error)}</p>
      ) : !reports.data ? (
        <Card padding="px-5 py-10" className="text-center text-[14px] text-grey-secondary">
          Loading reports…
        </Card>
      ) : list.length === 0 ? (
        <Card padding="px-5 py-10" className="text-center text-[14px] text-grey-secondary">
          {canCreate
            ? "No reports yet. Create one to send a project's state at the end of each day."
            : "No day reports yet. A project's scrum master, a manager or an admin sets them up."}
        </Card>
      ) : (
        <>
          <ReportList reports={list} selectedId={selected?.report_id ?? null} onSelect={select} />
          {selected ? (
            <div ref={detailRef} className="scroll-mt-24">
              <ReportDetail
                key={selected.report_id}
                report={selected}
                setup={setup.data ?? null}
                onEdit={() => setEditing(selected)}
                onRemove={() => setRemoving(selected)}
                onChanged={refresh}
              />
            </div>
          ) : null}
        </>
      )}

      {editing && setup.data ? (
        <ReportDialog
          key={editing === "new" ? "new" : editing.report_id}
          report={editing === "new" ? null : editing}
          setup={setup.data}
          onClose={() => setEditing(null)}
          onSaved={async (saved) => {
            await refresh();
            select(saved.report_id);
          }}
        />
      ) : null}
      <ConfirmDialog
        state={
          removing
            ? {
                open: true,
                title: `Remove ${removing.name}?`,
                description: "The report stops going out, and its send history is deleted.",
                confirmLabel: "Remove",
                destructive: true,
                onConfirm: () => remove.mutate(removing.report_id),
              }
            : { open: false }
        }
        onOpenChange={(open) => (open ? undefined : setRemoving(null))}
      />
    </div>
  );
}

function ReportList({
  reports,
  selectedId,
  onSelect,
}: {
  reports: DayReportResponse[];
  selectedId: string | null;
  onSelect: (reportId: string, scroll: boolean) => void;
}) {
  return (
    <nav aria-label="Day reports">
      <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {reports.map((report) => {
          const active = report.report_id === selectedId;
          return (
            <li key={report.report_id} className="min-w-0">
              <button
                type="button"
                aria-current={active ? "true" : undefined}
                onClick={() => onSelect(report.report_id, true)}
                className={cn(
                  "flex h-full w-full flex-col gap-2 rounded-3xl border bg-white p-4 text-left transition-colors",
                  active
                    ? "border-magenta ring-1 ring-magenta"
                    : "border-grey-border hover:bg-grey-fill",
                )}
              >
                <span className="flex flex-wrap items-center gap-2">
                  <span className="min-w-0 break-words text-[16px] font-bold">{report.name}</span>
                  {report.enabled ? null : (
                    <RagChip tone="neutral" className="h-6 text-[12px]">
                      Off
                    </RagChip>
                  )}
                </span>
                <span className="text-[13px] text-grey-secondary">
                  {report.project_name ?? "A removed project"}
                  {report.release_id ? ` · release ${report.release_name ?? "removed"}` : ""}
                </span>
                <span className="text-[13px] text-grey-secondary">
                  {scheduleLabel(
                    report.schedule.local_time,
                    report.schedule.timezone,
                    report.schedule.weekdays,
                  )}
                </span>
                <LastSend report={report} />
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

function LastSend({ report }: { report: DayReportResponse }) {
  if (!report.last_run) {
    return (
      <RagChip tone="neutral" className="h-6 self-start text-[12px]">
        Not sent yet
      </RagChip>
    );
  }
  return (
    <RagChip tone={RUN_TONES[report.last_run.status]} dot className="h-6 self-start text-[12px]">
      {RUN_LABELS[report.last_run.status]} {formatReportDay(report.last_run.report_date)}
    </RagChip>
  );
}

function ReportDetail({
  report,
  setup,
  onEdit,
  onRemove,
  onChanged,
}: {
  report: DayReportResponse;
  setup: DayReportSetupResponse | null;
  onEdit: () => void;
  onRemove: () => void;
  onChanged: () => Promise<unknown>;
}) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const preview = useQuery({
    queryKey: ["day-reports", "preview", report.report_id],
    queryFn: () => apiClient.previewDayReport(report.report_id),
  });
  const sendNow = useMutation({
    mutationFn: () => apiClient.sendDayReport(report.report_id),
    onSuccess: async (run) => {
      await Promise.all([
        onChanged(),
        queryClient.invalidateQueries({ queryKey: reportRunsQueryKey(report.report_id) }),
      ]);
      const message = `${RUN_LABELS[run.status]}: ${run.title}`;
      if (run.status === "sent") toast.success(message);
      else toast.error(`${message}. Its history says why.`);
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const people = peopleLine(report.audience);
  const confirmation = sendConfirmation(report);

  return (
    <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,380px)] lg:grid-rows-[auto_1fr]">
      <Card padding="p-5" className="flex flex-col gap-3 lg:col-start-2 lg:row-start-1">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="min-w-0 break-words text-[18px] font-bold">{report.name}</h2>
          {report.enabled ? null : (
            <RagChip tone="neutral" className="h-6 text-[12px]">
              Off
            </RagChip>
          )}
        </div>
        <dl className="grid grid-cols-[88px_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[13px]">
          <dt className="text-grey-secondary">Project</dt>
          <dd className="break-words">
            {report.project_name ?? "A removed project"}
            {report.release_id ? `, only release ${report.release_name ?? "(removed)"}` : ""}
          </dd>
          <dt className="text-grey-secondary">When</dt>
          <dd>
            {report.enabled
              ? scheduleLabel(
                  report.schedule.local_time,
                  report.schedule.timezone,
                  report.schedule.weekdays,
                )
              : "Switched off: it is sent only when someone presses Send now."}
          </dd>
          <dt className="text-grey-secondary">Goes to</dt>
          <dd className="break-words">
            {report.audience_summary}
            {people ? <span className="block text-grey-secondary">{people}</span> : null}
          </dd>
        </dl>
        {report.can_send || report.can_edit ? (
          <div className="flex flex-wrap gap-2 pt-1">
            {report.can_send ? (
              <Pill
                size="sm"
                disabled={sendNow.isPending || report.destination_count === 0}
                title={report.destination_count === 0 ? "It goes nowhere yet." : undefined}
                onClick={() => setConfirming(true)}
              >
                <Send size={14} />
                {sendNow.isPending ? "Sending…" : "Send now"}
              </Pill>
            ) : null}
            {report.can_edit ? (
              <>
                <Pill variant="ghost" size="sm" disabled={!setup} onClick={onEdit}>
                  <Pencil size={14} />
                  Edit
                </Pill>
                <Pill variant="ghost" size="sm" onClick={onRemove}>
                  <Trash2 size={14} />
                  Remove
                </Pill>
              </>
            ) : null}
          </div>
        ) : (
          <p className="text-[12px] text-grey-secondary">
            The project&apos;s scrum master, a manager or an admin sends it and sets it up.
          </p>
        )}
      </Card>

      <Card
        padding="p-5 sm:p-6"
        className="flex min-w-0 flex-col gap-4 lg:col-start-1 lg:row-span-2 lg:row-start-1"
      >
        <div>
          <h2 className="text-[13px] font-bold uppercase tracking-wide text-magenta">
            Today&apos;s report
          </h2>
          <p className="mt-1 text-[12px] text-grey-secondary">
            What it would say if it were sent now, built from today&apos;s state. Nothing is sent.
          </p>
        </div>
        {preview.isError ? (
          <p className="text-[14px] text-rag-red">{errorMessage(preview.error)}</p>
        ) : !preview.data ? (
          <p className="text-[14px] text-grey-secondary">Building the report…</p>
        ) : (
          <ReportPreview preview={preview.data} />
        )}
      </Card>

      <div className="flex min-w-0 flex-col gap-6 lg:col-start-2 lg:row-start-2">
        <Card padding="p-5" className="flex flex-col gap-3">
          <div>
            <h3 className="text-[16px] font-bold">Today&apos;s note</h3>
            <p className="mt-1 text-[12px] text-grey-secondary">
              What the report says first: the news, or what is needed. It goes out with today&apos;s
              report only.
            </p>
          </div>
          <ReportNoteEditor
            key={report.note?.updated_at ?? "none"}
            report={report}
            onSaved={async () => {
              await Promise.all([
                onChanged(),
                queryClient.invalidateQueries({
                  queryKey: ["day-reports", "preview", report.report_id],
                }),
              ]);
            }}
          />
        </Card>
        <Card padding="p-5" className="flex flex-col gap-3">
          <h3 className="text-[16px] font-bold">Sent before</h3>
          <ReportHistory reportId={report.report_id} />
        </Card>
      </div>

      <ConfirmDialog
        state={
          confirming
            ? {
                open: true,
                title: confirmation.title,
                description: confirmation.description,
                confirmLabel: "Send now",
                onConfirm: () => sendNow.mutate(),
              }
            : { open: false }
        }
        onOpenChange={(open) => (open ? undefined : setConfirming(false))}
      />
    </div>
  );
}
