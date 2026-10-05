import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, History, Pencil, Plus, Send, Trash2, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type {
  ConfigNodeResponse,
  DayReportResponse,
  DestinationKind,
  ReportDestinationOptionResponse,
  ReportRunResponse,
} from "../../api/schema";
import { Card } from "../../components/ui/Card";
import { TextArea, TextInput } from "../../components/ui/Field";
import { Modal } from "../../components/ui/Modal";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import type { BadgeTone } from "../../lib/status";
import { cn } from "../../lib/utils";
import { AdminSelect, NodeSelect } from "./AdminSelect";
import { ConfirmDialog } from "./ConfirmDialog";
import { FormField } from "./FormField";
import { errorMessage, weekdayOptions } from "./adminTypes";
import {
  type ReportForm,
  destinationsSummary,
  emailDestinations,
  emptyReportForm,
  formFromReport,
  formProblems,
  requestFromForm,
  scheduleLabel,
} from "./reportForm";

const REPORTS_QUERY_KEY = ["config", "day-reports"] as const;
const DESTINATIONS_QUERY_KEY = ["config", "report-destinations"] as const;

const RUN_TONES: Record<ReportRunResponse["status"], BadgeTone> = {
  sent: "success",
  partial: "warning",
  failed: "danger",
  sending: "info",
};
const RUN_LABELS: Record<ReportRunResponse["status"], string> = {
  sent: "Sent",
  partial: "Partly sent",
  failed: "Not sent",
  sending: "Sending",
};

/**
 * Day reports: a project's state, sent on its own schedule to the people who act on it.
 *
 * Each report goes out once per local day at its time, to chat channels,
 * people, email addresses (mailing lists included) or the Teams channel, as
 * the Integrations tab has them set up.
 */
export function DayReportsPanel({
  projects,
  members,
}: {
  projects: ConfigNodeResponse[];
  members: ConfigNodeResponse[];
}) {
  const queryClient = useQueryClient();
  const reports = useQuery({ queryKey: REPORTS_QUERY_KEY, queryFn: apiClient.dayReports });
  const options = useQuery({
    queryKey: DESTINATIONS_QUERY_KEY,
    queryFn: apiClient.reportDestinationOptions,
  });
  const [editing, setEditing] = useState<DayReportResponse | "new" | null>(null);
  const [previewing, setPreviewing] = useState<DayReportResponse | null>(null);
  const [history, setHistory] = useState<DayReportResponse | null>(null);
  const [removing, setRemoving] = useState<DayReportResponse | null>(null);
  const [sending, setSending] = useState<DayReportResponse | null>(null);

  const refresh = () => queryClient.invalidateQueries({ queryKey: REPORTS_QUERY_KEY });
  const remove = useMutation({
    mutationFn: (reportId: string) => apiClient.removeDayReport(reportId),
    onSuccess: async () => {
      await refresh();
      toast.success("Report removed.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const sendNow = useMutation({
    mutationFn: (reportId: string) => apiClient.sendDayReport(reportId),
    onSuccess: async (run) => {
      await refresh();
      const message = `${RUN_LABELS[run.status]}: ${run.title}`;
      if (run.status === "sent") toast.success(message);
      else toast.error(`${message}. Open its history for why.`);
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const projectName = (id: string) => projects.find((item) => item.id === id)?.name ?? id;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-[18px] font-bold">Day reports</h2>
          <p className="mt-1 max-w-[680px] text-[13px] text-grey-secondary">
            A project&apos;s progress, requirements by stage, blockers, dependencies and risk
            signals, each with who resolves it, sent once a day at the time you set. Destinations
            use the connections on the Integrations tab.
          </p>
        </div>
        <Pill size="sm" onClick={() => setEditing("new")} disabled={projects.length === 0}>
          <Plus size={14} />
          New report
        </Pill>
      </div>

      {reports.isError ? (
        <p className="text-[14px] text-rag-red">{errorMessage(reports.error)}</p>
      ) : !reports.data ? (
        <Card padding="px-5 py-10" className="text-center text-[14px] text-grey-secondary">
          Loading reports…
        </Card>
      ) : reports.data.length === 0 ? (
        <Card padding="px-5 py-10" className="text-center text-[14px] text-grey-secondary">
          {projects.length === 0
            ? "Add a project on the Entities tab first; a report covers one project."
            : "No reports yet. Create one to send a project's state at the end of each day."}
        </Card>
      ) : (
        <div className="flex flex-col gap-3">
          {reports.data.map((report) => (
            <Card key={report.report_id} padding="p-5" className="flex flex-col gap-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-[16px] font-bold">{report.name}</span>
                    {report.enabled ? null : (
                      <RagChip tone="neutral" className="h-6 text-[12px]">
                        Off
                      </RagChip>
                    )}
                  </div>
                  <p className="mt-0.5 text-[13px] text-grey-secondary">
                    {projectName(report.project_id)}
                    {report.release_id ? ", one release" : ""} ·{" "}
                    {scheduleLabel(
                      report.schedule.local_time,
                      report.schedule.timezone,
                      report.schedule.weekdays,
                    )}{" "}
                    · {destinationsSummary(report.destinations)}
                  </p>
                </div>
                {report.last_run ? (
                  <RagChip tone={RUN_TONES[report.last_run.status]} dot>
                    {RUN_LABELS[report.last_run.status]} {formatDay(report.last_run.report_date)}
                  </RagChip>
                ) : (
                  <RagChip tone="neutral">Not sent yet</RagChip>
                )}
              </div>
              <div className="flex flex-wrap gap-2">
                <Pill variant="ghost" size="sm" onClick={() => setPreviewing(report)}>
                  <Eye size={14} />
                  Preview
                </Pill>
                <Pill
                  variant="ghost"
                  size="sm"
                  disabled={sendNow.isPending || report.destinations.length === 0}
                  onClick={() => setSending(report)}
                >
                  <Send size={14} />
                  Send now
                </Pill>
                <Pill variant="ghost" size="sm" onClick={() => setHistory(report)}>
                  <History size={14} />
                  History
                </Pill>
                <Pill variant="ghost" size="sm" onClick={() => setEditing(report)}>
                  <Pencil size={14} />
                  Edit
                </Pill>
                <Pill variant="ghost" size="sm" onClick={() => setRemoving(report)}>
                  <Trash2 size={14} />
                  Remove
                </Pill>
              </div>
            </Card>
          ))}
        </div>
      )}

      {editing ? (
        <ReportDialog
          key={editing === "new" ? "new" : editing.report_id}
          report={editing === "new" ? null : editing}
          projects={projects}
          members={members}
          options={options.data ?? []}
          onClose={() => setEditing(null)}
          onSaved={refresh}
        />
      ) : null}
      {previewing ? (
        <PreviewDialog report={previewing} onClose={() => setPreviewing(null)} />
      ) : null}
      {history ? (
        <HistoryDialog report={history} members={members} onClose={() => setHistory(null)} />
      ) : null}
      <ConfirmDialog
        state={
          sending
            ? {
                open: true,
                title: `Send ${sending.name} now?`,
                description: `It goes to ${destinationsSummary(sending.destinations)} right away. Today's scheduled send still goes out at its time.`,
                confirmLabel: "Send now",
                onConfirm: () => sendNow.mutate(sending.report_id),
              }
            : { open: false }
        }
        onOpenChange={(open) => (open ? undefined : setSending(null))}
      />
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

function ReportDialog({
  report,
  projects,
  members,
  options,
  onClose,
  onSaved,
}: {
  report: DayReportResponse | null;
  projects: ConfigNodeResponse[];
  members: ConfigNodeResponse[];
  options: ReportDestinationOptionResponse[];
  onClose: () => void;
  onSaved: () => Promise<unknown>;
}) {
  const [form, setForm] = useState<ReportForm>(() =>
    report ? formFromReport(report) : emptyReportForm(browserTimezone()),
  );
  const [emails, setEmails] = useState("");
  const [shown, setShown] = useState(false);
  const problems = formProblems(form);

  const save = useMutation({
    mutationFn: () =>
      report
        ? apiClient.updateDayReport(report.report_id, requestFromForm(form))
        : apiClient.createDayReport(requestFromForm(form)),
    onSuccess: async () => {
      await onSaved();
      toast.success("Report saved.");
      onClose();
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const add = (kind: DestinationKind, target = "") =>
    setForm({ ...form, destinations: [...form.destinations, { kind, target }] });
  const available = (kind: DestinationKind) =>
    options.find((option) => option.kind === kind) ?? { available: kind === "person", note: "" };

  return (
    <Modal
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      title={report ? "Edit report" : "New report"}
    >
      <form
        className="flex max-h-[72vh] flex-col gap-4 overflow-y-auto pr-1"
        onSubmit={(event) => {
          event.preventDefault();
          setShown(true);
          if (problems.length === 0) save.mutate();
        }}
      >
        <FormField label="Name" htmlFor="report-name">
          <TextInput
            id="report-name"
            value={form.name}
            placeholder="Checkout Revamp: end of day"
            onChange={(event) => setForm({ ...form, name: event.target.value })}
          />
        </FormField>
        <FormField label="Project" htmlFor="report-project">
          <NodeSelect
            id="report-project"
            items={projects}
            value={form.projectId}
            placeholder="Pick a project"
            onChange={(projectId) => setForm({ ...form, projectId, releaseId: "" })}
          />
        </FormField>
        {form.projectId ? (
          <ReleaseField
            projectId={form.projectId}
            value={form.releaseId}
            onChange={(releaseId) => setForm({ ...form, releaseId })}
          />
        ) : null}
        <div className="grid grid-cols-[120px_1fr] gap-3">
          <FormField label="Send at" htmlFor="report-time">
            <TextInput
              id="report-time"
              type="time"
              value={form.time}
              onChange={(event) => setForm({ ...form, time: event.target.value })}
            />
          </FormField>
          <FormField label="Timezone" htmlFor="report-timezone">
            <TextInput
              id="report-timezone"
              value={form.timezone}
              placeholder="Europe/Berlin"
              onChange={(event) => setForm({ ...form, timezone: event.target.value })}
            />
          </FormField>
        </div>
        <div>
          <span className="text-[13px] font-bold text-grey-secondary">Weekdays</span>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {weekdayOptions.map((day) => {
              const on = form.weekdays.includes(day.value);
              return (
                <button
                  key={day.value}
                  type="button"
                  aria-pressed={on}
                  className={cn(
                    "h-9 rounded-full px-3.5 text-[13px] font-bold",
                    on
                      ? "bg-ink text-white"
                      : "border border-grey-border bg-white hover:bg-grey-fill",
                  )}
                  onClick={() =>
                    setForm({
                      ...form,
                      weekdays: on
                        ? form.weekdays.filter((value) => value !== day.value)
                        : [...form.weekdays, day.value],
                    })
                  }
                >
                  {day.label}
                </button>
              );
            })}
          </div>
        </div>

        <div className="flex flex-col gap-2 rounded-2xl bg-grey-fill p-4">
          <span className="text-[14px] font-bold">Send to</span>
          {form.destinations.length === 0 ? (
            <span className="text-[13px] text-grey-secondary">Nowhere yet.</span>
          ) : (
            form.destinations.map((destination, index) => (
              <div key={`${destination.kind}-${index}`} className="flex items-center gap-2">
                <span className="w-[112px] shrink-0 text-[12px] font-bold uppercase tracking-wide text-grey-secondary">
                  {destination.kind === "chat_channel"
                    ? "Chat channel"
                    : destination.kind === "person"
                      ? "Person"
                      : destination.kind === "email"
                        ? "Email"
                        : "Teams"}
                </span>
                {destination.kind === "person" ? (
                  <NodeSelect
                    items={members}
                    value={destination.target}
                    placeholder="Pick a member"
                    onChange={(target) => {
                      const destinations = [...form.destinations];
                      destinations[index] = { ...destination, target };
                      setForm({ ...form, destinations });
                    }}
                  />
                ) : destination.kind === "teams" ? (
                  <span className="flex-1 text-[14px]">The Teams connection&apos;s channel</span>
                ) : (
                  <TextInput
                    className="py-2 text-[14px]"
                    value={destination.target}
                    placeholder={destination.kind === "email" ? "team@example.com" : "C0123456789"}
                    onChange={(event) => {
                      const destinations = [...form.destinations];
                      destinations[index] = { ...destination, target: event.target.value };
                      setForm({ ...form, destinations });
                    }}
                  />
                )}
                <button
                  type="button"
                  aria-label="Remove this destination"
                  className="rounded-full p-2 text-grey-secondary hover:bg-grey-hover hover:text-ink"
                  onClick={() =>
                    setForm({
                      ...form,
                      destinations: form.destinations.filter((_, item) => item !== index),
                    })
                  }
                >
                  <X size={14} />
                </button>
              </div>
            ))
          )}
          <div className="mt-1 flex flex-wrap gap-2">
            <Pill variant="ghost" size="sm" onClick={() => add("person")}>
              <Plus size={14} />
              Person
            </Pill>
            <DestinationButton
              label="Chat channel"
              option={available("chat_channel")}
              onClick={() => add("chat_channel")}
            />
            <DestinationButton
              label="Teams channel"
              option={available("teams")}
              disabled={form.destinations.some((item) => item.kind === "teams")}
              onClick={() => add("teams")}
            />
          </div>
          <FormField label="Email addresses or mailing lists" htmlFor="report-emails">
            <div className="flex items-start gap-2">
              <TextArea
                id="report-emails"
                className="min-h-[64px] py-2 text-[14px]"
                value={emails}
                placeholder="delivery-leads@example.com, cto-office@example.com"
                onChange={(event) => setEmails(event.target.value)}
              />
              <Pill
                variant="ghost"
                size="sm"
                disabled={!available("email").available || emailDestinations(emails).length === 0}
                onClick={() => {
                  const existing = new Set(
                    form.destinations
                      .filter((item) => item.kind === "email")
                      .map((item) => item.target),
                  );
                  setForm({
                    ...form,
                    destinations: [
                      ...form.destinations,
                      ...emailDestinations(emails).filter((item) => !existing.has(item.target)),
                    ],
                  });
                  setEmails("");
                }}
              >
                Add
              </Pill>
            </div>
          </FormField>
          {(["chat_channel", "email", "teams"] as const)
            .map((kind) => available(kind))
            .filter((option) => !option.available && option.note)
            .map((option) => (
              <p key={option.note} className="text-[12px] text-grey-secondary">
                {"label" in option ? `${option.label}: ` : ""}
                {option.note}
              </p>
            ))}
        </div>

        <label className="flex items-center gap-3 text-[14px] font-bold">
          <input
            type="checkbox"
            className="h-4 w-4 accent-[var(--op-magenta)]"
            checked={form.enabled}
            onChange={(event) => setForm({ ...form, enabled: event.target.checked })}
          />
          On: send it on schedule
        </label>

        {shown && problems.length > 0 ? (
          <ul className="list-disc pl-5 text-[13px] text-rag-red">
            {problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        ) : null}

        <div className="flex justify-end gap-2">
          <Pill variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Pill>
          <Pill type="submit" size="sm" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save report"}
          </Pill>
        </div>
      </form>
    </Modal>
  );
}

function DestinationButton({
  label,
  option,
  disabled,
  onClick,
}: {
  label: string;
  option: { available: boolean; note: string };
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <Pill
      variant="ghost"
      size="sm"
      disabled={!option.available || disabled}
      title={option.available ? undefined : option.note}
      onClick={onClick}
    >
      <Plus size={14} />
      {label}
    </Pill>
  );
}

function ReleaseField({
  projectId,
  value,
  onChange,
}: {
  projectId: string;
  value: string;
  onChange: (releaseId: string) => void;
}) {
  const releases = useQuery({
    queryKey: ["persona", "releases", projectId],
    queryFn: () => apiClient.releases(projectId),
  });
  const items = releases.data ?? [];
  if (items.length === 0 && !value) return null;
  return (
    <FormField label="Covers" htmlFor="report-release">
      <AdminSelect
        id="report-release"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">The whole project</option>
        {items.map((release) => (
          <option key={release.release_id} value={release.release_id}>
            Only release {release.name}
          </option>
        ))}
      </AdminSelect>
    </FormField>
  );
}

function PreviewDialog({ report, onClose }: { report: DayReportResponse; onClose: () => void }) {
  const preview = useQuery({
    queryKey: ["config", "day-report-preview", report.report_id],
    queryFn: () => apiClient.previewDayReport(report.report_id),
  });
  return (
    <Modal
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      title={`Preview: ${report.name}`}
    >
      {preview.isError ? (
        <p className="text-[14px] text-rag-red">{errorMessage(preview.error)}</p>
      ) : !preview.data ? (
        <p className="text-[14px] text-grey-secondary">Building the report…</p>
      ) : (
        <div className="flex max-h-[68vh] flex-col gap-3 overflow-y-auto pr-1 text-[14px]">
          <p className="text-[12px] text-grey-secondary">
            What it would say if it were sent now. Nothing is sent.
          </p>
          <pre className="whitespace-pre-wrap rounded-2xl bg-grey-fill p-4 font-sans text-[13px] leading-relaxed">
            {preview.data.text}
          </pre>
        </div>
      )}
    </Modal>
  );
}

function HistoryDialog({
  report,
  members,
  onClose,
}: {
  report: DayReportResponse;
  members: ConfigNodeResponse[];
  onClose: () => void;
}) {
  const runs = useQuery({
    queryKey: ["config", "day-report-runs", report.report_id],
    queryFn: () => apiClient.dayReportRuns(report.report_id),
  });
  const target = (kind: DestinationKind, value: string) =>
    kind === "person"
      ? (members.find((item) => item.id === value)?.name ?? value)
      : value || "Teams";
  return (
    <Modal
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      title={`History: ${report.name}`}
    >
      {runs.isError ? (
        <p className="text-[14px] text-rag-red">{errorMessage(runs.error)}</p>
      ) : !runs.data ? (
        <p className="text-[14px] text-grey-secondary">Loading…</p>
      ) : runs.data.length === 0 ? (
        <p className="text-[14px] text-grey-secondary">It has not been sent yet.</p>
      ) : (
        <ul className="flex max-h-[68vh] flex-col gap-3 overflow-y-auto pr-1">
          {runs.data.map((run) => (
            <li key={run.run_id} className="rounded-2xl border border-grey-border p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-[14px] font-bold">
                  {formatDay(run.report_date)} ·{" "}
                  {run.trigger === "manual" ? `sent by ${run.actor ?? "an admin"}` : "on schedule"}
                </span>
                <RagChip tone={RUN_TONES[run.status]} dot className="h-6 text-[12px]">
                  {RUN_LABELS[run.status]}
                </RagChip>
              </div>
              <ul className="mt-2 flex flex-col gap-1 text-[13px]">
                {run.outcomes.map((outcome, index) => (
                  <li key={index} className={outcome.ok ? "text-grey-body" : "text-rag-red"}>
                    {target(outcome.kind, outcome.target)}: {outcome.detail}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </Modal>
  );
}

function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function formatDay(iso: string): string {
  return new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
  });
}
