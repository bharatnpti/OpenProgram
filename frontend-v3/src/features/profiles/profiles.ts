// Profiles: the discipline a person brings to a pod (development, QA, product...). Pure helpers,
// type imports only so `node --test` runs them. A profile grants no rights: it routes who is
// asked and named. It is part of the pod membership, so a person can hold a different one in
// each pod, and it is never set on its own: a suggestion is shown, with why, and a person picks.
import type {
  MemberProfileResponse,
  ProfileResponse,
  ProfileReviewItemResponse,
  ProfileSuggestionResponse,
} from "../../api/schema";

/** The profile a pod role stands for, as the backend's default and backfill read it. */
export const POD_ROLE_PROFILES: Record<string, string> = {
  developer: "development",
  product_owner: "product",
  scrum_master: "delivery",
  manager: "management",
};

/** "scrum_master" as "delivery"; any other role is development, as on the server. */
export function profileForPodRole(role: string | null | undefined): string {
  const key = (role ?? "").trim().toLowerCase().replace(/ /g, "_");
  return POD_ROLE_PROFILES[key] ?? "development";
}

/** The entries a person may pick: not hidden, in the catalog's order. */
export function offeredProfiles(catalog: readonly ProfileResponse[]): ProfileResponse[] {
  return catalog
    .filter((entry) => !entry.hidden)
    .sort((a, b) => a.sort - b.sort || a.label.localeCompare(b.label));
}

/**
 * The picker's options: the offered entries, and the one held now when it is hidden, so a
 * membership that holds a hidden profile still shows it (it can be kept, not picked again).
 */
export function profileOptions(
  catalog: readonly ProfileResponse[],
  current?: string | null,
): { value: string; label: string; disabled?: boolean }[] {
  const options: { value: string; label: string; disabled?: boolean }[] = offeredProfiles(
    catalog,
  ).map((entry) => ({ value: entry.slug, label: entry.label }));
  if (current && !options.some((option) => option.value === current)) {
    options.push({
      value: current,
      label: `${profileLabel(catalog, current)} (hidden)`,
      disabled: true,
    });
  }
  return options;
}

/** The catalog's label; a slug it no longer lists, in words. */
export function profileLabel(catalog: readonly ProfileResponse[], slug: string): string {
  const known = catalog.find((entry) => entry.slug === slug);
  if (known) return known.label;
  const words = slug.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Someone chose it: an admin, the pod's scrum master, an import, an accepted suggestion. */
export function isChecked(member: Pick<MemberProfileResponse, "source">): boolean {
  return member.source !== "pod_role" && member.source !== "backfill";
}

/**
 * Where the profile comes from, in a line: "From their pod role; nobody checked it yet.",
 * "Set by Asha Rao on Mon 12 Oct." `day` formats an ISO date for the reader.
 */
export function sourceWords(
  member: Pick<MemberProfileResponse, "source" | "set_by_name" | "since">,
  day: (iso: string) => string,
): string {
  if (!isChecked(member)) return "From their pod role; nobody checked it yet.";
  const on = member.since ? ` on ${day(member.since)}` : "";
  const by = member.set_by_name ? ` by ${member.set_by_name}` : "";
  if (member.source === "import") return `Set when they were imported${on}.`;
  if (member.source === "suggestion_accepted") return `Suggestion accepted${by}${on}.`;
  return `Set${by}${on}.`;
}

/** What a chip says after the name: "Developer · QA and test", "· Development (from the role)". */
export function chipNote(
  roleLabel: string,
  member: Pick<MemberProfileResponse, "label" | "source"> | undefined,
): string | undefined {
  if (!member) return roleLabel || undefined;
  const profile = isChecked(member) ? member.label : `${member.label} (from the role)`;
  return roleLabel ? `${roleLabel} · ${profile}` : profile;
}

/** "Suggested: QA and test, because their title says 'QA Engineer'." */
export function suggestionLine(suggestion: ProfileSuggestionResponse): string {
  const why = suggestion.reasons.length > 0 ? `, because ${joinAnd(suggestion.reasons)}` : "";
  return `Suggested: ${suggestion.label}${why}.`;
}

/** The review list's opening line, or null when nothing is left to check. */
export function reviewSummary(count: number): string | null {
  if (count === 0) return null;
  return count === 1
    ? "1 profile comes from the pod role and nobody has checked it yet."
    : `${count} profiles come from the pod role and nobody has checked them yet.`;
}

/** The suggestions that would change a profile, then the ones that only confirm it. */
export function reviewOrder(
  items: readonly ProfileReviewItemResponse[],
): ProfileReviewItemResponse[] {
  const changes = (item: ProfileReviewItemResponse) =>
    item.suggestion.profile !== item.current.profile ? 0 : 1;
  return [...items].sort(
    (a, b) =>
      changes(a) - changes(b) ||
      a.current.pod_name.localeCompare(b.current.pod_name) ||
      a.current.member_name.localeCompare(b.current.member_name),
  );
}

/** "Accept 4 suggestions?" and what it does, before Accept all runs. */
export function acceptAllWords(items: readonly ProfileReviewItemResponse[]): {
  title: string;
  effect: string;
  names: string[];
} {
  const changing = items.filter((item) => item.suggestion.profile !== item.current.profile).length;
  const n = items.length;
  const changes =
    changing === 0
      ? "Each keeps the profile it has; it is marked as checked."
      : `${changing} of ${n} change; the rest keep the profile they have and are marked as checked.`;
  return {
    title: `Accept ${n} ${n === 1 ? "suggestion" : "suggestions"}?`,
    effect: `${changes} Each change holds from today; earlier days keep the profile they had. A profile grants no rights: it only says who is asked and named.`,
    names: items.map(
      (item) => `${item.current.member_name} in ${item.current.pod_name}: ${item.suggestion.label}`,
    ),
  };
}

/** The toast after a change: "Sofia Bergmann is now QA and test in Payments Pod." */
export function profileDone(
  member: Pick<MemberProfileResponse, "member_name" | "label" | "pod_name">,
): string {
  return `${member.member_name} is now ${member.label} in ${member.pod_name}.`;
}

/** The import's confirm line when people also join a pod. */
export function importPlacementWords(
  count: number,
  podName: string,
  roleLabel: string,
  profile: string | null,
): string {
  const who = count === 1 ? "They join" : "They all join";
  const as = profile
    ? ` with the ${profile} profile`
    : ", their profile read from the role until someone sets it";
  return `${who} ${podName} as ${roleLabel.toLowerCase()}${as}. Someone already in the pod keeps their role.`;
}

/**
 * A refused profile change in words, for the scrum masters who reach it outside Admin: a 403
 * is "Not available to you", with the server's reason. Null for anything else, which the
 * caller says with the admin screens' own `refusal`.
 */
export function profileRefusal(error: unknown): string | null {
  const failed = (typeof error === "object" && error !== null ? error : {}) as {
    status?: number;
    message?: string;
  };
  if (failed.status !== 403) return null;
  const said = failed.message?.trim();
  return `Not available to you.${said ? ` The server said: ${said}` : ""}`;
}

function joinAnd(parts: readonly string[]): string {
  if (parts.length <= 1) return parts.join("");
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}
