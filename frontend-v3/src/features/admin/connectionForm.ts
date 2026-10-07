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

export function formFromConnection(connection: ConnectionResponse): ConnectionForm {
  const values: Record<string, string> = {};
  connection.fields.forEach((field) => {
    if (field.kind === "secret") return;
    values[field.key] = connection.settings[field.key] ?? field.default ?? "";
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

/** The unsaved values to test: typed secrets only; a blank one uses what is stored. */
export function testPayload(
  connection: ConnectionResponse,
  form: ConnectionForm,
): ConnectionTestRequest {
  const payload = savePayload(connection, form);
  const secrets: Record<string, string> = {};
  Object.entries(payload.secrets ?? {}).forEach(([key, value]) => {
    if (value) secrets[key] = value;
  });
  return { settings: payload.settings ?? {}, secrets };
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
