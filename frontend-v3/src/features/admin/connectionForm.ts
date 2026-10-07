// Only type imports here: node --test strips types but cannot resolve
// extensionless runtime imports.
import type {
  ConnectionResponse,
  ConnectionTestRequest,
  ConnectionUpdateRequest,
  ConnectorFieldDto,
  ConnectorFieldOptionDto,
  ConnectorPurpose,
  FieldConditionDto,
} from "../../api/schema";

/*
 * The connection editor. Secrets are write-only: the API never returns one,
 * only the names of the secret fields that hold a value (`secrets_set`). A
 * secret is sent only when it is typed in, or marked to clear; one left alone
 * keeps its stored value, so a save never needs the token again.
 */

/** What the dialog edits: plain values, plus secrets typed in or marked to clear. */
export type ConnectionForm = {
  enabled: boolean;
  values: Record<string, string>;
  secrets: Record<string, string>;
  clearedSecrets: string[];
};

/**
 * Whether the server's own settings run this connection: the tenant has none of
 * its own saved. A connection saved here replaces them.
 */
export function runsOnServerSettings(
  connection: Pick<ConnectionResponse, "configured" | "environment_configured">,
): boolean {
  return !connection.configured && connection.environment_configured;
}

export function formFromConnection(connection: ConnectionResponse): ConnectionForm {
  const values: Record<string, string> = {};
  const onServer = runsOnServerSettings(connection);
  connection.fields.forEach((field) => {
    if (field.kind === "secret") return;
    // A typical address ("https://gitlab.com") is no value for a connection the
    // server already runs elsewhere: typing a token over it would repoint the
    // tenant. The address starts empty, and has to be typed.
    const fallback = onServer && field.kind === "url" ? "" : (field.default ?? "");
    values[field.key] = connection.settings[field.key] ?? fallback;
  });
  return {
    enabled: connection.configured ? connection.enabled : true,
    values,
    secrets: {},
    clearedSecrets: [],
  };
}

export function conditionHolds(
  condition: FieldConditionDto | null | undefined,
  values: Record<string, string>,
): boolean {
  if (!condition) return true;
  return condition.values.includes(values[condition.field] ?? "");
}

/** The fields that apply under the options chosen so far (Cloud or Data Center sign-in). */
export function visibleFields(
  fields: ConnectorFieldDto[],
  values: Record<string, string>,
): ConnectorFieldDto[] {
  return fields.filter((field) => conditionHolds(field.shown_when, values));
}

export function visibleOptions(
  field: ConnectorFieldDto,
  values: Record<string, string>,
): ConnectorFieldOptionDto[] {
  return field.options.filter((option) => conditionHolds(option.shown_when, values));
}

/**
 * Pick a still-offered option when a choice elsewhere hides the current one,
 * e.g. switching Jira to Data Center moves the sign-in off "Email and API token".
 */
export function withValidOptions(
  fields: ConnectorFieldDto[],
  values: Record<string, string>,
): Record<string, string> {
  const next = { ...values };
  fields.forEach((field) => {
    if (field.kind !== "select" || !conditionHolds(field.shown_when, next)) return;
    const options = visibleOptions(field, next);
    if (options.length > 0 && !options.some((option) => option.value === next[field.key])) {
      next[field.key] = options[0].value;
    }
  });
  return next;
}

/** Which required fields that apply are still empty: what stops it being turned on. */
export function missingRequired(
  connection: ConnectionResponse,
  form: ConnectionForm,
): ConnectorFieldDto[] {
  return visibleFields(connection.fields, form.values).filter((field) => {
    if (!field.required) return false;
    if (field.kind === "secret") {
      const stored =
        connection.secrets_set.includes(field.key) && !form.clearedSecrets.includes(field.key);
      return !stored && !(form.secrets[field.key] ?? "").trim();
    }
    return !(form.values[field.key] ?? "").trim();
  });
}

/**
 * The save request. Every plain field is sent (blank clears it). Of the
 * secrets only those typed in, or marked to clear, are sent.
 */
export function savePayload(
  connection: ConnectionResponse,
  form: ConnectionForm,
): ConnectionUpdateRequest {
  const settings: Record<string, string | null> = {};
  const secrets: Record<string, string | null> = {};
  connection.fields.forEach((field) => {
    if (field.kind === "secret") {
      const typed = (form.secrets[field.key] ?? "").trim();
      if (typed) secrets[field.key] = typed;
      else if (form.clearedSecrets.includes(field.key)) secrets[field.key] = null;
      return;
    }
    const value = (form.values[field.key] ?? "").trim();
    settings[field.key] = value || null;
  });
  return { enabled: form.enabled, settings, secrets };
}

/**
 * Fields that decide where a stored secret is sent, or who it signs in as: an
 * address, a user, the port and transport, the deployment and the sign-in
 * method. Changing one of them while a stored secret is reused would send that
 * secret to an unsaved place.
 */
function routesSecrets(field: ConnectorFieldDto): boolean {
  return (
    field.kind === "url" ||
    field.kind === "email" ||
    ["host", "port", "security", "username", "deployment", "auth_method"].includes(field.key)
  );
}

function storedValue(connection: ConnectionResponse, field: ConnectorFieldDto): string {
  return (connection.settings[field.key] ?? field.default ?? "").trim();
}

/** The plain fields whose value differs from what is stored. */
function changedFields(connection: ConnectionResponse, form: ConnectionForm): ConnectorFieldDto[] {
  return connection.fields.filter(
    (field) =>
      field.kind !== "secret" &&
      (form.values[field.key] ?? "").trim() !== storedValue(connection, field),
  );
}

/**
 * Why Test cannot run on these values, or null when it can. A test of unsaved
 * values keeps every stored secret that is not typed again, and the server
 * sends it wherever those values point, so a changed address or user with a
 * stored secret left as it is would send that secret to an address nobody
 * saved. The server refuses that too (a 400 naming the fields); this says it
 * before the round trip.
 *
 * A secret marked to clear is not reused: the test carries it as null and the
 * server leaves it out, never taking it from the store, so it is no reason to
 * stop. It used to be one, when a cleared secret fell back to the stored one.
 */
export function testBlock(connection: ConnectionResponse, form: ConnectionForm): string | null {
  const typed = (key: string) => (form.secrets[key] ?? "").trim() !== "";
  const reused = connection.secrets_set.filter(
    (key) => !typed(key) && !form.clearedSecrets.includes(key),
  );
  const moved = changedFields(connection, form).filter(routesSecrets);
  if (reused.length > 0 && moved.length > 0) {
    // The server's own sentence (ConnectionService `_reuse_refusal`), so the line before the
    // round trip and the 400 after it read alike. Field labels only, never a value.
    const secretLabels = reused.map(
      (key) => connection.fields.find((field) => field.key === key)?.label ?? key,
    );
    return `${labelList(moved.map((field) => field.label))} changed. A stored secret is used only with the address and sign-in it was saved with, so enter ${labelList(secretLabels)} again to test the new values.`;
  }
  return null;
}

/** "A", "A and B", "A, B and C". */
function labelList(labels: string[]): string {
  return labels.length > 1
    ? `${labels.slice(0, -1).join(", ")} and ${labels[labels.length - 1]}`
    : (labels[0] ?? "");
}

/**
 * The test request. Nothing changed: no body, so the server tests what is
 * stored and records the result on the connection ("Last test passed").
 * Otherwise the unsaved values: a secret typed in is sent, one marked to clear
 * is sent as null (so the server tests without it and does not fall back to the
 * stored one), and one left alone is left out (the stored one is used, as long
 * as `testBlock` finds the address and sign-in unchanged).
 */
export function testPayload(
  connection: ConnectionResponse,
  form: ConnectionForm,
): ConnectionTestRequest | undefined {
  const typedAny = Object.values(form.secrets).some((value) => value.trim() !== "");
  if (
    connection.configured &&
    !typedAny &&
    form.clearedSecrets.length === 0 &&
    changedFields(connection, form).length === 0
  ) {
    return undefined;
  }
  const payload = savePayload(connection, form);
  return { settings: payload.settings ?? {}, secrets: payload.secrets ?? {} };
}

export type ConnectionState = "on" | "off" | "environment" | "not_set_up";

/** How a connector card reads at a glance. */
export function connectionState(connection: ConnectionResponse): ConnectionState {
  if (connection.configured && connection.enabled) return "on";
  if (connection.environment_configured) return "environment";
  if (connection.configured) return "off";
  return "not_set_up";
}

/**
 * Who last saved the connection: the member's name, which the server resolves,
 * or the stored id when it is no member's, never a guess.
 */
export function savedBy(connection: ConnectionResponse): string {
  return connection.updated_by_name ?? connection.updated_by ?? "an admin";
}

export type ConnectionGroup = {
  key: "reports" | "work" | "calendar" | "other";
  title: string;
  note: string;
};

export const CONNECTION_GROUPS: ConnectionGroup[] = [
  {
    key: "reports",
    title: "Where day reports go",
    note: "Chat, email and Teams: the ways a day report reaches people.",
  },
  {
    key: "work",
    title: "Where work is read from",
    note: "The issue tracker and the Git host. One Git host is on at a time.",
  },
  {
    key: "calendar",
    title: "Calendar",
    note: "Leave days, so a missing check-in on leave is not read as silence.",
  },
  { key: "other", title: "Other connections", note: "" },
];

const PURPOSE_GROUP: Record<ConnectorPurpose, ConnectionGroup["key"]> = {
  chat: "reports",
  report_delivery: "reports",
  issue_tracker: "work",
  code: "work",
  calendar: "calendar",
};

/** The group a connector is shown in, by what it is for. */
export function groupOf(connection: ConnectionResponse): ConnectionGroup["key"] {
  for (const purpose of connection.purposes) {
    const group = PURPOSE_GROUP[purpose];
    if (group) return group;
  }
  return "other";
}

/** "Jira Cloud" or "Jira Data Center or Server": the chosen option's words. */
export function optionLabel(connection: ConnectionResponse, key: string): string | null {
  const field = connection.fields.find((item) => item.key === key);
  const value = connection.settings[key];
  if (!field || !value) return null;
  return field.options.find((option) => option.value === value)?.label ?? value;
}
