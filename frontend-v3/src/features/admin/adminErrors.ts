// Plain words for a change the server refused. No runtime imports, so `node --test` can run it.

/** What a failed request carries: an `ApiError` has all three, a network failure only a message. */
export type FailedRequest = { status?: number; message?: string; detail?: unknown };

function asRequest(error: unknown): FailedRequest {
  if (typeof error === "object" && error !== null) return error as FailedRequest;
  return { message: typeof error === "string" ? error : undefined };
}

function sentence(text: string): string {
  const trimmed = text.trim();
  if (!trimmed) return trimmed;
  const capital = trimmed[0].toUpperCase() + trimmed.slice(1);
  return /[.!?]$/.test(capital) ? capital : `${capital}.`;
}

/** The request fields these screens send, as the screens call them. */
const FIELD_WORDS: Record<string, string> = {
  name: "name",
  description: "description",
  code: "short code",
  jira_project_key: "Jira project key",
  jira_board_id: "Jira board id",
  jira_base_jql: "Jira search",
  jira_filter_jql: "Jira filter",
  github_repos: "Git repositories",
  chat_user_id: "chat user id",
  jira_account_id: "Jira account id",
  jira_email: "Jira email",
  vcs_username: "Git username",
  role: "role",
  task_id: "task id",
  program_id: "program",
  external_ids: "people to import",
  member_id: "member",
  chat_external_id: "chat id",
};

/** A FastAPI 422 carries a list of problems; the first one, as "field: what is wrong". */
function validationLine(detail: unknown): string | null {
  const body = (detail as { detail?: unknown } | null | undefined)?.detail;
  if (!Array.isArray(body) || body.length === 0) return null;
  const first = body[0] as { loc?: unknown[]; msg?: string; type?: string };
  const key = String(first.loc?.[first.loc.length - 1] ?? "").trim();
  const field = FIELD_WORDS[key] ?? key.replace(/_/g, " ");
  if (first.type === "string_too_short") return `The ${field || "value"} can't be empty.`;
  return field && first.msg ? `${sentence(`${field}: ${first.msg}`)}` : (first.msg ?? null);
}

// The messages the config service raises, in the words an admin uses. Each rule matches the
// server's text; anything none of them knows is shown as the server said it.
const RULES: [RegExp, (match: RegExpMatchArray) => string][] = [
  [
    /^node (.+) already exists$/,
    (m) => `The id ${m[1]} is already taken. Choose a different name.`,
  ],
  [/^(?:contains|assigned_to) link already exists$/, () => "They are already linked."],
  [/^(.+) exists as a (.+), not a (.+)$/, (m) => `${m[1]} is a ${m[2]}, not a ${m[3]}.`],
  [
    /^(\w+) (.+) not found for tenant .+$/,
    (m) =>
      `There is no ${m[1].replace(/_/g, " ")} ${m[2]}. It may have been removed; refresh the list and try again.`,
  ],
  [
    /link .*was not found$|link for project .+ was not found$/,
    () => "They are not linked any more. Refresh the list.",
  ],
  [/^self links are not allowed$/, () => "Something can't be linked to itself."],
  [/^(\w+) must not be empty$/, (m) => `The ${m[1].replace(/_/g, " ")} can't be empty.`],
  [
    /^(.+) has no chat ID linked$/,
    (m) => `${m[1]} has no chat id yet, so they can't be a contact. Set one under Directory.`,
  ],
  [/^chat ID (.+) is not linked to any member$/, () => "That chat id is not linked to any member."],
  [/^an escalation contact needs a member$/, () => "Pick a member for each contact you keep."],
  [
    /^active directory user (.+) not found for tenant .+$/,
    (m) => `${m[1]} is not in the chat directory any more. Sync the directory and search again.`,
  ],
  [
    /^directory repository is not configured$/,
    () => "The chat directory is not set up for this tenant.",
  ],
  [
    /^identity link repository is not configured$/,
    () => "Identity links are not available on this deployment.",
  ],
];

/** What a refused or failed change means, in words; the server's own text when no rule knows it. */
export function refusal(error: unknown, fallback = "That did not work."): string {
  const request = asRequest(error);
  const server = request.message?.trim() ?? "";

  if (request.status === undefined || request.status === 0) {
    return /failed to fetch|networkerror|load failed/i.test(server) || !server
      ? "Could not reach the server. Check the connection and try again."
      : sentence(server);
  }
  if (request.status === 422) return validationLine(request.detail) ?? sentence(server || fallback);
  for (const [pattern, words] of RULES) {
    const match = server.match(pattern);
    if (match) return words(match);
  }
  if (request.status === 403) {
    return `Only an admin can change this.${server ? ` The server said: ${server}` : ""}`;
  }
  if (request.status === 404) {
    return "It no longer exists. Someone may have removed it; refresh the list and try again.";
  }
  if (request.status === 424) {
    return `A connection this needs is not set up. ${sentence(server)}`.trim();
  }
  if (request.status === 503) {
    return `A service this needs cannot be reached right now. ${sentence(server)}`.trim();
  }
  if (request.status >= 500) {
    return "The server hit an unexpected error. Refresh to see what was saved, then try again.";
  }
  return server ? sentence(server) : fallback;
}
