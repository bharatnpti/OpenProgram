// Who may change what in Reports, in the backend's terms and in plain words.
// Type imports only, so `node --test` runs it directly.
import type { ItemKindDto } from "../../api/schema";
import type { AppRole } from "../../app/role";

/**
 * Mirrors the backend's capabilities (core/application/authorization.py) for
 * the roles the API is told, so a control can say up front who uses it. The
 * backend still decides, per project and per pod: a 403 shows its reason.
 */
export type ReportAccess = {
  /** `READ_PROJECT_PROGRESS`: the forecast, requirements and releases. */
  readProjectProgress: boolean;
  /** `SET_PROJECT_DATES`: the project's and a release's date, releases, today's note. */
  setProjectDates: boolean;
  /** `SET_POD_DATES`: a pod's date; a scrum master only for a pod they run. */
  setPodDates: boolean;
  /** `EDIT_GATES`: keep, dismiss and add gate items and questions. */
  editGates: boolean;
  /** `GET /pods/{id}/delivery` takes either of the date capabilities' readers. */
  readPodDelivery: boolean;
};

export function accessFor(lens: readonly AppRole[]): ReportAccess {
  const has = (...roles: AppRole[]) =>
    lens.includes("admin") || roles.some((r) => lens.includes(r));
  const readProjectProgress = has("po", "mgr", "exec");
  const setPodDates = has("sm", "mgr");
  return {
    readProjectProgress,
    setProjectDates: has("po", "mgr"),
    setPodDates,
    editGates: has("dev", "sm", "po", "mgr"),
    readPodDelivery: readProjectProgress || setPodDates,
  };
}

/**
 * The same rights with every change switched off, for a past day being viewed.
 * Reads stay as they are: looking back is still looking.
 */
export function withoutWrites(access: ReportAccess): ReportAccess {
  return { ...access, setProjectDates: false, setPodDates: false, editGates: false };
}

/** Who does each thing, worded for "… opens for …" and "… is set by …". */
export const WHO = {
  projectProgress: "a product owner, manager, executive or admin",
  projectDates: "a product owner, manager or admin",
  podDates: "the pod's own scrum master, a manager or an admin",
  podDelivery: "a scrum master, product owner, manager, executive or admin",
  gates: "a developer, scrum master, product owner, manager or admin",
  reports: "a scrum master of one of the project's pods, a manager or an admin",
  note: "a product owner, manager or admin",
} as const;

const CAPABILITY_WHO: Record<string, string> = {
  read_project_progress: WHO.projectProgress,
  set_project_dates: WHO.projectDates,
  set_pod_dates: "a scrum master, manager or admin",
  edit_gates: WHO.gates,
  send_day_reports: WHO.reports,
  set_up_day_reports: WHO.reports,
  read_day_reports: "anyone with a member record",
  manage_config: "an admin",
};

const ROLE_WORDS: Record<string, string> = {
  dev: "developer",
  sm: "scrum master",
  po: "product owner",
  mgr: "manager",
  exec: "executive",
  admin: "admin",
};

/** An admin signs every kind off; anyone else only the kinds that name their role. */
export function maySignOff(
  kind: Pick<ItemKindDto, "sign_off_roles">,
  lens: readonly AppRole[],
): boolean {
  return lens.includes("admin") || kind.sign_off_roles.some((role) => lens.includes(role));
}

/** "a product owner or manager", for "Signed off by …". */
export function signOffWho(kind: Pick<ItemKindDto, "sign_off_roles">): string {
  const names = kind.sign_off_roles.map((role) => ROLE_WORDS[role] ?? role);
  if (names.length === 0) return "an admin";
  return `${/^[aeiou]/.test(names[0]) ? "an" : "a"} ${joinOr(names)}`;
}

function joinOr(names: string[]): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} or ${names[names.length - 1]}`;
}

type ErrorLike = { status?: unknown; message?: unknown; detail?: unknown };

const FIELD_WORDS: Record<string, string> = {
  target_date: "Delivery date",
  note: "Why",
  name: "Name",
  match_kind: "Defined by",
  match_value: "Fix version or label",
  text: "Text",
  summary: "What we asked",
  asked_to: "Asked to",
  evidence_url: "Evidence link",
  status: "Status",
};

/**
 * What a refused or failed change says to the person, in plain words. A 403
 * keeps the server's reason; when that reason is a capability name, it leads
 * with who may do it. A 422 is the server's own sentence, or each invalid
 * field by its label.
 */
export function actionError(error: unknown): string {
  const e = (typeof error === "object" && error !== null ? error : {}) as ErrorLike;
  const status = typeof e.status === "number" ? e.status : null;
  const server = typeof e.message === "string" && e.message.trim() ? e.message.trim() : "";
  if (status === 403) {
    const capability = /is not authorized for ([a-z_]+)/.exec(server)?.[1];
    const who = capability ? CAPABILITY_WHO[capability] : undefined;
    if (who) return `Only ${who} can do this. The server said: ${server}`;
    return server ? `Not allowed. The server said: ${server}` : "Not allowed for your role.";
  }
  if (status === 422) {
    return validationWords(e.detail) ?? (server || "The server could not take that.");
  }
  if (status === 404) return server ? `It is no longer there: ${server}` : "It is no longer there.";
  return server || "Something went wrong. Try again.";
}

function validationWords(body: unknown): string | null {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (!Array.isArray(detail) || detail.length === 0) return null;
  const lines = detail.map((item) => {
    const loc = Array.isArray(item?.loc) ? item.loc : [];
    const field = String(loc[loc.length - 1] ?? "");
    const message = typeof item?.msg === "string" ? item.msg : "is not valid";
    return `${FIELD_WORDS[field] ?? "A field"}: ${message.replace(/^Value error, /, "")}.`;
  });
  return lines.join(" ");
}
