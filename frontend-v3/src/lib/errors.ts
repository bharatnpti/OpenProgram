// Pure: no imports, so `node --test` can run it. Reads an error by its shape
// (`status`, `detail`), which is how the API client's ApiError looks.

type ApiLike = { status?: unknown; message?: unknown; detail?: unknown };

/** Names a field in a 422's `loc` the way a person would. */
const FIELD_WORDS: Record<string, string> = {
  summary: "The summary",
  description: "A blocker",
  eta_change_days: "The ETA change",
  blocker_items: "The blockers",
  status: "The status",
};

/**
 * What to tell a person when an action failed, in plain words.
 *
 * `doing` finishes "You can't …" for a refusal ("confirm this check-in"). A
 * validation error (422) names the field instead of "Request failed with
 * status 422", which is all the client says when the server sends a list.
 */
export function actionError(error: unknown, doing: string): string {
  const e = (typeof error === "object" && error !== null ? error : {}) as ApiLike;
  const message = typeof e.message === "string" && e.message ? e.message : "";
  const status = typeof e.status === "number" ? e.status : null;

  if (status === 403) {
    return `You can't ${doing} with your role.${message ? ` The server said: ${message}` : ""}`;
  }
  if (status === 422) {
    const first = validationItems(e.detail)[0];
    if (first) return validationSentence(first);
    return message || "Some of what you entered is not accepted.";
  }
  if (status === 401) return "You are signed out. Sign in again and retry.";
  if (status !== null && status >= 500) {
    return `The server could not ${doing} just now. Try again in a moment.`;
  }
  if (status === null) {
    // fetch rejects with a TypeError when nothing answered.
    return error instanceof TypeError
      ? "Could not reach the server. Check your connection and try again."
      : message || `Could not ${doing}.`;
  }
  return message || `Could not ${doing}.`;
}

type ValidationItem = { msg?: unknown; loc?: unknown };

function validationItems(detail: unknown): ValidationItem[] {
  const list =
    typeof detail === "object" && detail !== null && "detail" in detail
      ? (detail as { detail?: unknown }).detail
      : detail;
  return Array.isArray(list) ? (list as ValidationItem[]) : [];
}

function validationSentence(item: ValidationItem): string {
  const msg = typeof item.msg === "string" ? item.msg.replace(/^Value error, /, "") : "";
  const loc = Array.isArray(item.loc) ? item.loc.filter((x) => typeof x === "string") : [];
  const field = [...loc].reverse().find((name) => name in FIELD_WORDS);
  const who = field ? FIELD_WORDS[field] : "One of the fields";
  if (!msg) return `${who} is not accepted.`;
  return `${who} is not accepted: ${msg.charAt(0).toLowerCase()}${msg.slice(1)}${msg.endsWith(".") ? "" : "."}`;
}
