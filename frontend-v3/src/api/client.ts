import type {
  AuthStatusResponse,
  DayReportResponse,
  DevUsersResponse,
  DirectoryItemResponse,
  EscalationMatrixResponse,
  GateBoardResponse,
  LogoutResponse,
  ProjectDeliveryResponse,
  ProjectRisksResponse,
  ReportPreviewResponse,
  ReportRunResponse,
  RequirementsResponse,
} from "./schema";

/*
 * The same backend, auth and request conventions as frontend-v2's client
 * (cookies, CSRF header on writes, dev acting-as headers, correlation id), cut
 * down to what the reports app calls. Add methods here as screens need them.
 */

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";
const CSRF_COOKIE_NAME = import.meta.env.VITE_AUTH_CSRF_COOKIE_NAME ?? "openprogram_csrf";
const CSRF_HEADER_NAME = import.meta.env.VITE_AUTH_CSRF_HEADER_NAME ?? "x-csrf-token";
const DEV_USER_HEADER = "x-openprogram-dev-user";
const DEV_ROLES_HEADER = "x-openprogram-dev-roles";

/**
 * Who the app is acting as, for local demo tenants only. The backend honours
 * these headers exclusively under the dev auth provider in a local environment;
 * anywhere else they are ignored, so sending them is always safe.
 */
type ActingAs = { id: string; roles: string[] } | null;

let actingAs: ActingAs = null;

export function setActingAs(next: ActingAs): void {
  actingAs = next;
}

function actingAsHeaders(): Record<string, string> {
  if (!actingAs) {
    return {};
  }
  const headers: Record<string, string> = { [DEV_USER_HEADER]: actingAs.id };
  if (actingAs.roles.length > 0) {
    headers[DEV_ROLES_HEADER] = actingAs.roles.join(",");
  }
  return headers;
}

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function requestJson<T>(
  path: string,
  options: { method?: string; body?: unknown } = {},
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: options.method,
    credentials: "include",
    headers: {
      ...(options.body === undefined ? {} : { "Content-Type": "application/json" }),
      ...csrfHeader(options.method),
      ...actingAsHeaders(),
      "x-correlation-id": crypto.randomUUID(),
    },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  if (!response.ok) {
    const detail = await parseErrorBody(response);
    throw new ApiError(response.status, errorMessage(response.status, detail), detail);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

const id = encodeURIComponent;

export const apiClient = {
  // Auth
  authStatus: () => requestJson<AuthStatusResponse>("/api/v1/auth/status"),
  devUsers: () => requestJson<DevUsersResponse>("/api/v1/auth/dev-users"),
  logout: () => requestJson<LogoutResponse>("/api/v1/auth/logout", { method: "POST" }),

  // Directory
  projects: () => requestJson<DirectoryItemResponse[]>("/projects"),

  // Daily: day reports
  dayReports: (projectId?: string) =>
    requestJson<DayReportResponse[]>(withQuery("/day-reports", { project_id: projectId })),
  previewDayReport: (reportId: string) =>
    requestJson<ReportPreviewResponse>(`/day-reports/${id(reportId)}/preview`),
  sendDayReport: (reportId: string) =>
    requestJson<ReportRunResponse>(`/day-reports/${id(reportId)}/send`, { method: "POST" }),
  dayReportRuns: (reportId: string) =>
    requestJson<ReportRunResponse[]>(`/day-reports/${id(reportId)}/runs`),
  writeDayReportNote: (reportId: string, text: string) =>
    requestJson<DayReportResponse>(`/day-reports/${id(reportId)}/note`, {
      method: "PUT",
      body: { text },
    }),

  // Overall: delivery date, requirements, gates, risks, escalation
  projectDelivery: (projectId: string) =>
    requestJson<ProjectDeliveryResponse>(`/projects/${id(projectId)}/delivery`),
  projectRequirements: (projectId: string, days = 30) =>
    requestJson<RequirementsResponse>(
      withQuery(`/projects/${id(projectId)}/requirements`, { days: String(days) }),
    ),
  gateBoard: (projectId: string) =>
    requestJson<GateBoardResponse>(`/projects/${id(projectId)}/gates`),
  projectRisks: (projectId: string) =>
    requestJson<ProjectRisksResponse>(`/projects/${id(projectId)}/risks`),
  projectEscalation: (projectId: string) =>
    requestJson<EscalationMatrixResponse>(`/config/escalation/projects/${id(projectId)}`),
};

function withQuery(path: string, params: Record<string, string | undefined>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value) {
      search.set(key, value);
    }
  });
  const query = search.toString();
  return query ? `${path}?${query}` : path;
}

function isWriteMethod(method?: string): boolean {
  return ["POST", "PUT", "PATCH", "DELETE"].includes((method ?? "GET").toUpperCase());
}

function csrfHeader(method?: string): Record<string, string> {
  if (!isWriteMethod(method)) {
    return {};
  }
  const token = readCookie(CSRF_COOKIE_NAME);
  return token ? { [CSRF_HEADER_NAME]: token } : {};
}

function readCookie(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`;
  return (
    document.cookie
      .split(";")
      .map((item) => item.trim())
      .find((item) => item.startsWith(prefix))
      ?.slice(prefix.length) ?? null
  );
}

async function parseErrorBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  try {
    if (contentType.includes("application/json")) {
      return await response.json();
    }
    return await response.text();
  } catch {
    return undefined;
  }
}

function errorMessage(status: number, detail: unknown): string {
  if (typeof detail === "object" && detail !== null && "detail" in detail) {
    const value = (detail as { detail?: unknown }).detail;
    if (typeof value === "string") {
      return value;
    }
  }
  if (typeof detail === "string" && detail.trim()) {
    return detail;
  }
  if (status === 403) {
    return "Your role does not include this view.";
  }
  if (status === 404) {
    return "Not found.";
  }
  return `Request failed with status ${status}.`;
}
