import { useMutation } from "@tanstack/react-query";
import { Plus, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type {
  DayReportResponse,
  DayReportSetupResponse,
  DestinationKind,
  ReportSetupPersonResponse,
} from "../../api/schema";
import { TextArea, TextInput } from "../../components/ui/Field";
import { Modal } from "../../components/ui/Modal";
import { Pill } from "../../components/ui/Pill";
import { cn } from "../../lib/utils";
import { AdminSelect } from "../admin/AdminSelect";
import { FormField } from "../admin/FormField";
import { errorMessage, weekdayOptions } from "../admin/adminTypes";
import {
  type ReportForm,
  emailDestinations,
  emptyReportForm,
  formFromReport,
  formProblems,
  requestFromForm,
} from "./reportForm";

const KIND_LABELS: Record<DestinationKind, string> = {
  chat_channel: "Chat channel",
  person: "Person",
  email: "Email",
  teams: "Teams",
};

/**
 * Create or change a day report: one project (or one release of it), when it
 * goes out, and where. The projects offered are the ones the caller may set
 * reports up for; destinations use the connections set up under Integrations.
 */
export function ReportDialog({
  report,
  setup,
  onClose,
  onSaved,
}: {
  report: DayReportResponse | null;
  setup: DayReportSetupResponse;
  onClose: () => void;
  onSaved: (saved: DayReportResponse) => Promise<unknown>;
}) {
  const [form, setForm] = useState<ReportForm>(() =>
    report ? formFromReport(report) : emptyReportForm(browserTimezone()),
  );
  const [emails, setEmails] = useState("");
  const [shown, setShown] = useState(false);
  const problems = formProblems(form);
  const project = setup.projects.find((item) => item.id === form.projectId);
  const options = setup.destinations;

  const save = useMutation({
    mutationFn: () =>
      report
        ? apiClient.updateDayReport(report.report_id, requestFromForm(form))
        : apiClient.createDayReport(requestFromForm(form)),
    onSuccess: async (saved) => {
      await onSaved(saved);
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
      wide
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
          <AdminSelect
            id="report-project"
            value={form.projectId}
            onChange={(event) => setForm({ ...form, projectId: event.target.value, releaseId: "" })}
          >
            <option value="">Pick a project</option>
            {setup.projects.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </AdminSelect>
        </FormField>
        {project && (project.releases.length > 0 || form.releaseId) ? (
          <FormField label="Covers" htmlFor="report-release">
            <AdminSelect
              id="report-release"
              value={form.releaseId}
              onChange={(event) => setForm({ ...form, releaseId: event.target.value })}
            >
              <option value="">The whole project</option>
              {project.releases.map((release) => (
                <option key={release.release_id} value={release.release_id}>
                  Only release {release.name}
                </option>
              ))}
            </AdminSelect>
          </FormField>
        ) : null}
        <div className="grid grid-cols-1 gap-3 min-[420px]:grid-cols-[120px_minmax(0,1fr)]">
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
              <div
                key={`${destination.kind}-${index}`}
                className="flex flex-wrap items-center gap-2 sm:flex-nowrap"
              >
                <span className="w-[96px] shrink-0 text-[12px] font-bold uppercase tracking-wide text-grey-secondary">
                  {KIND_LABELS[destination.kind]}
                </span>
                <div className="min-w-0 flex-1 basis-[160px]">
                  {destination.kind === "person" ? (
                    <PersonSelect
                      people={setup.people}
                      value={destination.target}
                      onChange={(target) => {
                        const destinations = [...form.destinations];
                        destinations[index] = { ...destination, target };
                        setForm({ ...form, destinations });
                      }}
                    />
                  ) : destination.kind === "teams" ? (
                    <span className="text-[14px]">The Teams connection&apos;s channel</span>
                  ) : (
                    <TextInput
                      className="py-2 text-[14px]"
                      value={destination.target}
                      placeholder={
                        destination.kind === "email" ? "team@example.com" : "C0123456789"
                      }
                      onChange={(event) => {
                        const destinations = [...form.destinations];
                        destinations[index] = { ...destination, target: event.target.value };
                        setForm({ ...form, destinations });
                      }}
                    />
                  )}
                </div>
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
            <div className="flex flex-wrap items-start gap-2 sm:flex-nowrap">
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
          {options
            .filter((option) => option.kind !== "person" && !option.available && option.note)
            .map((option) => (
              <p key={option.kind} className="text-[12px] text-grey-secondary">
                {option.label}: {option.note}
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

function PersonSelect({
  people,
  value,
  onChange,
}: {
  people: ReportSetupPersonResponse[];
  value: string;
  onChange: (value: string) => void;
}) {
  // A stored person who is no member any more stays picked, shown by id.
  const known = !value || people.some((person) => person.id === value);
  return (
    <AdminSelect
      className="py-2 text-[14px]"
      value={value}
      aria-label="Person"
      onChange={(event) => onChange(event.target.value)}
    >
      <option value="">Pick a member</option>
      {known ? null : <option value={value}>{value}</option>}
      {people.map((person) => (
        <option key={person.id} value={person.id}>
          {person.name}
        </option>
      ))}
    </AdminSelect>
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

function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}
