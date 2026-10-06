// Only type imports here: node --test strips types but cannot resolve
// extensionless runtime imports.
import type {
  DayReportRequest,
  DayReportResponse,
  DestinationKind,
  ReportDestinationDto,
  ReportRunResponse,
} from "../../api/schema";

export type ReportForm = {
  name: string;
  projectId: string;
  /** "" reports on the whole project. */
  releaseId: string;
  enabled: boolean;
  time: string;
  timezone: string;
  weekdays: number[];
  destinations: ReportDestinationDto[];
};

const WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function emptyReportForm(timezone: string): ReportForm {
  return {
    name: "",
    projectId: "",
    releaseId: "",
    enabled: true,
    time: "18:00",
    timezone,
    weekdays: [0, 1, 2, 3, 4],
    destinations: [],
  };
}

export function formFromReport(report: DayReportResponse): ReportForm {
  return {
    name: report.name,
    projectId: report.project_id,
    releaseId: report.release_id ?? "",
    enabled: report.enabled,
    time: report.schedule.local_time.slice(0, 5),
    timezone: report.schedule.timezone,
    weekdays: [...report.schedule.weekdays],
    destinations: report.destinations.map((item) => ({ ...item })),
  };
}

export function requestFromForm(form: ReportForm): DayReportRequest {
  return {
    name: form.name.trim(),
    project_id: form.projectId,
    release_id: form.releaseId || null,
    enabled: form.enabled,
    schedule: {
      local_time: form.time.length === 5 ? `${form.time}:00` : form.time,
      timezone: form.timezone.trim(),
      weekdays: [...form.weekdays].sort((a, b) => a - b),
    },
    destinations: form.destinations
      .map((item) => ({ kind: item.kind, target: item.target.trim() }))
      .filter((item) => item.kind === "teams" || item.target),
  };
}

/** What stops the form being saved, in the words the dialog shows; empty when it can save. */
export function formProblems(form: ReportForm): string[] {
  const problems: string[] = [];
  if (!form.name.trim()) problems.push("Give the report a name.");
  if (!form.projectId) problems.push("Pick the project it reports on.");
  if (form.weekdays.length === 0) problems.push("Pick at least one weekday.");
  if (!/^\d{2}:\d{2}$/.test(form.time)) problems.push("Set a send time.");
  const destinations = requestFromForm(form).destinations ?? [];
  if (form.enabled && destinations.length === 0) {
    problems.push("Add somewhere for it to go, or switch it off.");
  }
  return problems;
}

/** Email addresses typed one per line or comma-separated, as one destination each. */
export function emailDestinations(text: string): ReportDestinationDto[] {
  const seen = new Set<string>();
  return text
    .split(/[\s,;]+/)
    .map((item) => item.trim().toLowerCase())
    .filter((item) => item.includes("@") && !seen.has(item) && Boolean(seen.add(item)))
    .map((target) => ({ kind: "email" as DestinationKind, target }));
}

/** "Mon–Fri at 18:00 (Europe/Berlin)", "Mon, Wed at 09:30 (UTC)", "Every day at …". */
export function scheduleLabel(time: string, timezone: string, weekdays: number[]): string {
  const days = [...new Set(weekdays)].sort((a, b) => a - b);
  let label: string;
  if (days.length === 7) label = "Every day";
  else if (
    days.length > 2 &&
    days.every((day, index) => index === 0 || day === days[index - 1] + 1)
  ) {
    label = `${WEEKDAY_NAMES[days[0]]}–${WEEKDAY_NAMES[days[days.length - 1]]}`;
  } else label = days.map((day) => WEEKDAY_NAMES[day]).join(", ") || "No day";
  return `${label} at ${time.slice(0, 5)} (${timezone})`;
}

/**
 * How a send started, as its history line reads: "on schedule", or "sent by"
 * the member's name, which the server resolves, else the stored id when it is
 * no member's, never a guess.
 */
export function runStartedBy(run: ReportRunResponse): string {
  if (run.trigger !== "manual") return "on schedule";
  return `sent by ${run.actor_name ?? run.actor ?? "a team member"}`;
}
