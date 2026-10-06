import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { DayReportRequest, DayReportResponse } from "../../api/schema";
import { useReadOnly } from "../../app/viewingDate";
import { LockedTrigger } from "../../components/Dialogs";
import { Pill } from "../../components/ui/Pill";
import { cn } from "../../lib/utils";
import { releaseName } from "../overall/overallWords";
import { actionError } from "./access";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const field = "h-10 w-full rounded-xl border border-grey-border bg-white px-3 text-[14px]";
const label = "mb-1 block text-[12px] font-bold uppercase tracking-wide text-grey-secondary";

type Draft = {
  name: string;
  projectId: string;
  releaseId: string;
  enabled: boolean;
  time: string;
  timezone: string;
  weekdays: number[];
  channels: string;
  people: string[];
  emails: string;
  teams: boolean;
};

function draftOf(report: DayReportResponse | undefined, projectId: string | undefined): Draft {
  const dest = report?.destinations ?? [];
  const of = (kind: string) => dest.filter((d) => d.kind === kind).map((d) => d.target);
  return {
    name: report?.name ?? "",
    projectId: report?.project_id ?? projectId ?? "",
    releaseId: report?.release_id ?? "",
    enabled: report?.enabled ?? true,
    time: report?.schedule.local_time.slice(0, 5) ?? "17:30",
    timezone:
      report?.schedule.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone ?? "UTC",
    weekdays: report?.schedule.weekdays ?? [0, 1, 2, 3, 4],
    channels: of("chat_channel").join(", "),
    people: of("person"),
    emails: of("email").join(", "),
    teams: of("teams").length > 0 || dest.some((d) => d.kind === "teams"),
  };
}

function requestOf(draft: Draft, projectName: string): DayReportRequest {
  const list = (text: string) =>
    text
      .split(/[,\n]/)
      .map((item) => item.trim())
      .filter(Boolean);
  return {
    name: draft.name.trim() || `${projectName}: end of day`,
    project_id: draft.projectId,
    release_id: draft.releaseId || null,
    enabled: draft.enabled,
    schedule: { local_time: draft.time, timezone: draft.timezone.trim(), weekdays: draft.weekdays },
    destinations: [
      ...list(draft.channels).map((target) => ({ kind: "chat_channel" as const, target })),
      ...draft.people.map((target) => ({ kind: "person" as const, target })),
      ...list(draft.emails).map((target) => ({ kind: "email" as const, target })),
      ...(draft.teams ? [{ kind: "teams" as const, target: "" }] : []),
    ],
  };
}

/**
 * Create a day report, or change or remove one. The server says which
 * projects this person may set reports up for, who can be sent a direct
 * message, and which destination kinds are connected; an unconnected kind is
 * shown with the reason and cannot be picked.
 */
export function ReportSetupDialog({
  trigger,
  report,
  projectId,
}: {
  trigger: ReactNode;
  report?: DayReportResponse;
  projectId?: string;
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Draft>(() => draftOf(report, projectId));
  const [confirmRemove, setConfirmRemove] = useState(false);

  const setup = useQuery({
    queryKey: ["day-reports", "setup"],
    queryFn: () => apiClient.dayReportSetup(),
    enabled: open,
  });
  const projects = setup.data?.projects ?? [];
  const project = projects.find((p) => p.id === draft.projectId);
  const available = (kind: string) =>
    setup.data?.destinations.find((d) => d.kind === kind) ?? { available: false, note: "" };

  const done = (message: string, removedId?: string) => {
    toast.success(message);
    if (removedId) {
      // Drop it from the lists first, so the page moves to another report
      // instead of asking for the removed one's preview and sends again.
      for (const key of [
        ["day-reports", "all"],
        ["day-reports", "project", report?.project_id],
      ]) {
        queryClient.setQueryData<DayReportResponse[]>(key, (old) =>
          old?.filter((item) => item.report_id !== removedId),
        );
      }
    }
    void queryClient.invalidateQueries({
      queryKey: ["day-reports"],
      predicate: (query) => !removedId || !query.queryKey.includes(removedId),
    });
    setOpen(false);
  };
  const save = useMutation({
    mutationFn: () => {
      const body = requestOf(draft, project?.name ?? "Project");
      return report
        ? apiClient.updateDayReport(report.report_id, body)
        : apiClient.createDayReport(body);
    },
    onSuccess: (saved) => done(report ? `Saved ${saved.name}.` : `Set up ${saved.name}.`),
    onError: (error) => toast.error(actionError(error)),
  });
  const remove = useMutation({
    mutationFn: () => apiClient.removeDayReport(report!.report_id),
    onSuccess: () => done("Report removed. Past sends are kept.", report?.report_id),
    onError: (error) => toast.error(actionError(error)),
  });

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((current) => ({ ...current, [key]: value }));

  // Setting up, changing and removing are changes: off while a past day is shown.
  const { readOnly, reason } = useReadOnly();
  if (readOnly) return <LockedTrigger trigger={trigger} reason={reason} />;

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          setDraft(draftOf(report, projectId));
          setConfirmRemove(false);
        }
      }}
    >
      <Dialog.Trigger asChild>{trigger}</Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[min(94vw,620px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-3xl bg-white p-6 shadow-op-palette animate-op-pop">
          <Dialog.Title className="text-[20px] font-extrabold">
            {report ? `Change ${report.name}` : "Set up a day report"}
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-[13px] text-grey-body">
            Sent once per local day at its time, to every destination below. Nothing is sent when
            you save.
          </Dialog.Description>

          {setup.isLoading ? (
            <p className="mt-4 text-grey-secondary">Loading…</p>
          ) : setup.error ? (
            <p className="mt-4 rounded-2xl bg-rag-red-bg p-3 text-[14px] text-rag-red">
              {(setup.error as Error).message}
            </p>
          ) : (
            <form
              className="mt-5 grid grid-cols-[minmax(0,1fr)] gap-4"
              onSubmit={(event) => {
                event.preventDefault();
                save.mutate();
              }}
            >
              <div className="grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-2">
                <div>
                  <label htmlFor="rs-project" className={label}>
                    Project
                  </label>
                  <select
                    id="rs-project"
                    className={field}
                    value={draft.projectId}
                    required
                    onChange={(e) =>
                      setDraft((d) => ({ ...d, projectId: e.target.value, releaseId: "" }))
                    }
                  >
                    <option value="" disabled>
                      Pick a project
                    </option>
                    {projects.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label htmlFor="rs-release" className={label}>
                    Covers
                  </label>
                  <select
                    id="rs-release"
                    className={field}
                    value={draft.releaseId}
                    onChange={(e) => set("releaseId", e.target.value)}
                  >
                    <option value="">The whole project</option>
                    {(project?.releases ?? []).map((r) => (
                      <option key={r.release_id} value={r.release_id}>
                        {releaseName(r.name)}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div>
                <label htmlFor="rs-name" className={label}>
                  Name
                </label>
                <input
                  id="rs-name"
                  className={field}
                  value={draft.name}
                  placeholder={`${project?.name ?? "Project"}: end of day`}
                  onChange={(e) => set("name", e.target.value)}
                />
              </div>

              <div className="grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-[120px_minmax(0,1fr)]">
                <div>
                  <label htmlFor="rs-time" className={label}>
                    Send at
                  </label>
                  <input
                    id="rs-time"
                    type="time"
                    className={field}
                    value={draft.time}
                    required
                    onChange={(e) => set("time", e.target.value)}
                  />
                </div>
                <div>
                  <label htmlFor="rs-tz" className={label}>
                    Time zone
                  </label>
                  <input
                    id="rs-tz"
                    className={field}
                    value={draft.timezone}
                    required
                    onChange={(e) => set("timezone", e.target.value)}
                  />
                </div>
              </div>

              <fieldset>
                <legend className={label}>On these days</legend>
                <div className="flex flex-wrap gap-1.5">
                  {WEEKDAYS.map((day, index) => {
                    const on = draft.weekdays.includes(index);
                    return (
                      <button
                        key={day}
                        type="button"
                        aria-pressed={on}
                        className={cn(
                          "h-9 rounded-full border px-3 text-[13px] font-bold",
                          on ? "border-ink bg-ink text-white" : "border-grey-border text-grey-body",
                        )}
                        onClick={() =>
                          set(
                            "weekdays",
                            on
                              ? draft.weekdays.filter((d) => d !== index)
                              : [...draft.weekdays, index].sort(),
                          )
                        }
                      >
                        {day}
                      </button>
                    );
                  })}
                </div>
              </fieldset>

              <fieldset className="grid grid-cols-[minmax(0,1fr)] gap-3">
                <legend className={label}>Goes to</legend>
                <Destination
                  kind="chat_channel"
                  option={available("chat_channel")}
                  title="Chat channels"
                >
                  <input
                    id="rs-channels"
                    aria-label="Chat channels"
                    className={field}
                    placeholder="#checkout-delivery"
                    value={draft.channels}
                    onChange={(e) => set("channels", e.target.value)}
                  />
                </Destination>
                <Destination
                  kind="person"
                  option={available("person")}
                  title="People by direct message"
                >
                  <div className="flex max-h-40 flex-wrap gap-1.5 overflow-y-auto">
                    {(setup.data?.people ?? []).map((person) => {
                      const on = draft.people.includes(person.id);
                      return (
                        <button
                          key={person.id}
                          type="button"
                          aria-pressed={on}
                          className={cn(
                            "h-8 rounded-full border px-3 text-[12px] font-bold",
                            on
                              ? "border-ink bg-ink text-white"
                              : "border-grey-border text-grey-body",
                          )}
                          onClick={() =>
                            set(
                              "people",
                              on
                                ? draft.people.filter((id) => id !== person.id)
                                : [...draft.people, person.id],
                            )
                          }
                        >
                          {person.name}
                        </button>
                      );
                    })}
                  </div>
                </Destination>
                <Destination
                  kind="email"
                  option={available("email")}
                  title="Email addresses or lists"
                >
                  <input
                    id="rs-emails"
                    aria-label="Email addresses"
                    className={field}
                    placeholder="checkout-leads@example.com"
                    value={draft.emails}
                    onChange={(e) => set("emails", e.target.value)}
                  />
                </Destination>
                <Destination kind="teams" option={available("teams")} title="Teams channel">
                  <label className="flex items-center gap-2 text-[14px]">
                    <input
                      type="checkbox"
                      checked={draft.teams}
                      onChange={(e) => set("teams", e.target.checked)}
                    />
                    Post to the connected Teams channel
                  </label>
                </Destination>
              </fieldset>

              <label className="flex items-center gap-2 text-[14px]">
                <input
                  type="checkbox"
                  checked={draft.enabled}
                  onChange={(e) => set("enabled", e.target.checked)}
                />
                On: send it on schedule
              </label>

              <div className="flex flex-wrap items-center justify-between gap-2 pt-2">
                {report ? (
                  confirmRemove ? (
                    <span className="flex items-center gap-2 text-[13px]">
                      Remove this report?
                      <Pill type="button" size="sm" variant="dark" onClick={() => remove.mutate()}>
                        Remove
                      </Pill>
                      <Pill
                        type="button"
                        size="sm"
                        variant="ghost"
                        onClick={() => setConfirmRemove(false)}
                      >
                        Keep it
                      </Pill>
                    </span>
                  ) : (
                    <button
                      type="button"
                      className="text-[13px] font-bold text-rag-red"
                      onClick={() => setConfirmRemove(true)}
                    >
                      Remove report
                    </button>
                  )
                ) : (
                  <span />
                )}
                <div className="flex gap-2">
                  <Dialog.Close asChild>
                    <Pill type="button" variant="ghost" size="sm">
                      Cancel
                    </Pill>
                  </Dialog.Close>
                  <Pill type="submit" size="sm" disabled={save.isPending || !draft.projectId}>
                    {save.isPending ? "Saving…" : report ? "Save" : "Set up report"}
                  </Pill>
                </div>
              </div>
            </form>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function Destination({
  title,
  option,
  children,
}: {
  kind: string;
  title: string;
  option: { available: boolean; note: string };
  children: ReactNode;
}) {
  return (
    <div
      className={cn("rounded-2xl border border-grey-border p-3", !option.available && "opacity-60")}
    >
      <p className="mb-2 text-[13px] font-bold">{title}</p>
      {option.available ? (
        children
      ) : (
        <p className="text-[12px] text-grey-secondary">
          Not available: {option.note || "not connected on Admin → Integrations."}
        </p>
      )}
    </div>
  );
}
