// Only type imports here: node --test strips types but cannot resolve
// extensionless runtime imports.
import type {
  ConnectionResponse,
  ConnectionTestRequest,
  ConnectionUpdateRequest,
  ConnectorFieldDto,
  ConnectorFieldOptionDto,
  FieldConditionDto,
} from "../../api/schema";

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

/** The fields that apply under the options chosen so far. */
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
 * secrets only those typed in, or marked to clear, are sent: one left alone
 * keeps its stored value, so a save never needs the token again.
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
 * The unsaved values to test, with the secrets exactly as a save would send
 * them: typed ones, and null for one marked to clear, so the test never uses
 * the stored value of a secret being removed. A secret left alone is reused
 * only while the address and sign-in are as saved; the server refuses the test
 * otherwise and says which field changed.
 */
export function testPayload(
  connection: ConnectionResponse,
  form: ConnectionForm,
): ConnectionTestRequest {
  const payload = savePayload(connection, form);
  return { settings: payload.settings ?? {}, secrets: payload.secrets ?? {} };
}

export type ConnectionState = "on" | "off" | "environment" | "not_set_up";

/**
 * Who last saved the connection: the member's name, which the server resolves,
 * or the stored id when it is no member's, never a guess.
 */
export function savedBy(connection: ConnectionResponse): string {
  return connection.updated_by_name ?? connection.updated_by ?? "an admin";
}

/** How a connector card reads at a glance. */
export function connectionState(connection: ConnectionResponse): ConnectionState {
  if (connection.configured && connection.enabled) return "on";
  if (connection.environment_configured) return "environment";
  if (connection.configured) return "off";
  return "not_set_up";
}
