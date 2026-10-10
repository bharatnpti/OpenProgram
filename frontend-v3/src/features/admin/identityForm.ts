// People and their accounts: the directory sync, the import and the identity link form, as pure
// wording and request building. Type imports only, so `node --test` can run it.
import type {
  DirectorySyncResponse,
  IdentityAutoMatchResponse,
  IdentityLinkResponse,
  IdentityLinkUpdateRequest,
} from "../../api/schema";
import { listNames, plural } from "./structure.ts";

export type IdentityForm = {
  chatUserId: string;
  jiraAccountId: string;
  jiraEmail: string;
  vcsUsername: string;
};

type LinkFields = Pick<
  IdentityLinkResponse,
  "chat_user_id" | "jira_account_id" | "jira_email" | "vcs_username"
>;

/** The four ids, what each is for, and the request field it is stored in. */
export const IDENTITY_FIELDS: {
  form: keyof IdentityForm;
  field: keyof LinkFields;
  label: string;
  help: string;
  placeholder?: string;
}[] = [
  {
    form: "chatUserId",
    field: "chat_user_id",
    label: "Chat user id",
    help: "How the bot finds this person to message them. Without it they are never asked to check in.",
    placeholder: "U1001",
  },
  {
    form: "jiraAccountId",
    field: "jira_account_id",
    label: "Jira account id",
    help: "How the Jira sync matches issues to this person. Without it their issues are not found.",
  },
  {
    form: "jiraEmail",
    field: "jira_email",
    label: "Jira email",
    help: "The address their Jira account uses. Filling in from the directory finds the account from it.",
    placeholder: "name@example.com",
  },
  {
    form: "vcsUsername",
    field: "vcs_username",
    label: "Git username",
    help: "Their login in GitHub or GitLab, so commits and merge requests are theirs.",
  },
];

export function formFromLink(link: LinkFields | null | undefined): IdentityForm {
  return {
    chatUserId: link?.chat_user_id ?? "",
    jiraAccountId: link?.jira_account_id ?? "",
    jiraEmail: link?.jira_email ?? "",
    vcsUsername: link?.vcs_username ?? "",
  };
}

const orNull = (value: string | null | undefined): string | null =>
  (value ?? "").trim() === "" ? null : (value ?? "").trim();

/** Only the ids that changed; a blank clears one. The server keeps every id that is left out. */
export function buildIdentityUpdate(
  link: LinkFields | null | undefined,
  form: IdentityForm,
): IdentityLinkUpdateRequest {
  const update: IdentityLinkUpdateRequest = {};
  for (const { form: key, field } of IDENTITY_FIELDS) {
    const next = orNull(form[key]);
    if (next !== orNull(link?.[field])) update[field] = next;
  }
  return update;
}

export function validateIdentity(form: IdentityForm): Partial<Record<keyof IdentityForm, string>> {
  const errors: Partial<Record<keyof IdentityForm, string>> = {};
  const email = form.jiraEmail.trim();
  if (email !== "" && !/^[^\s@]+@[^\s@]+$/.test(email)) {
    errors.jiraEmail = "Use an address such as name@example.com.";
  }
  for (const { form: key } of IDENTITY_FIELDS) {
    if (/\s/.test(form[key].trim()) && key !== "jiraEmail") {
      errors[key] = "An id has no spaces in it.";
    }
  }
  return errors;
}

const GAP_WORDS: Record<string, string> = {
  chat_user_id: "chat id",
  jira_account_id: "Jira account",
  jira_email: "Jira email",
  vcs_username: "Git username",
};

/** "chat id and Jira account" for the field names the server uses. */
export function gapWords(fields: readonly string[]): string {
  return listNames(fields.map((field) => GAP_WORDS[field] ?? field.replace(/_/g, " ")));
}

/** What is still missing for this person: the accounts the console needs to reach and match them. */
export function identityGaps(link: LinkFields | null | undefined): string[] {
  return (["chat_user_id", "jira_account_id", "vcs_username"] as const).filter(
    (field) => orNull(link?.[field]) === null,
  );
}

/**
 * "Wed 7 Oct 00:06": one moment in one time zone, the viewer's unless one is given, so the day
 * and the time can never disagree near midnight. "never" for a moment that has not happened.
 */
export function stamp(iso: string | null | undefined, timeZone?: string): string {
  if (!iso) return "never";
  const parts = new Intl.DateTimeFormat("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone,
  }).formatToParts(new Date(iso));
  const part = (type: string) => parts.find((item) => item.type === type)?.value ?? "";
  return `${part("weekday")} ${part("day")} ${part("month")} ${part("hour")}:${part("minute")}`;
}

/** "Synced: 14 people found, 1 no longer in the directory." */
export function syncSummary(
  result: Pick<DirectorySyncResponse, "synced_count" | "deactivated_count">,
) {
  const found = `${plural(result.synced_count, "person", "people")} found in the chat directory`;
  return result.deactivated_count > 0
    ? `${found}; ${result.deactivated_count} left the directory and can't be imported.`
    : `${found}. Nobody has left it since the last sync.`;
}

/** "Imported 2 people as members." Anyone who was already a member is left as they were. */
export function importSummary(imported: number, alreadyMembers: number): string {
  const added = `Imported ${plural(imported, "person", "people")} as members.`;
  return alreadyMembers > 0 ? `${added} ${alreadyMembers} already were.` : added;
}

/** What the auto-match filled in, member by member. */
export function autoMatchSummary(result: IdentityAutoMatchResponse): string {
  if (result.updated_count === 0)
    return "Nothing to fill in: every member already has what the directory knows.";
  const who = result.members.map((member) => `${member.name} (${gapWords(member.filled)})`);
  return `Filled in ${plural(result.updated_count, "member")}: ${who.join(", ")}.`;
}
