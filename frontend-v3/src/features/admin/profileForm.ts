// Admin › Profiles: rename, hide or add a profile. Pure helpers, type imports only so
// `node --test` runs them. Limits mirror core/domain/profiles.py (validated_entry and
// ProfileService.save_catalog_entry), so the form says what the server would refuse.
import type { ProfileResponse, ProfileUpdateRequest } from "../../api/schema";

export const MAX_LABEL = 40;
export const MAX_TITLE_WORDS = 12;
export const MAX_TITLE_WORD = 40;
export const MAX_PROFILES = 30;
const SLUG = /^[a-z][a-z0-9_]{0,31}$/;

export type ProfileDraft = {
  label: string;
  codeWork: boolean;
  /** Offered in the pickers; unticked, the profile is hidden. */
  offered: boolean;
  titleWordsText: string;
  sort: number;
};

export function draftFromProfile(entry: ProfileResponse): ProfileDraft {
  return {
    label: entry.label,
    codeWork: entry.code_work,
    offered: !entry.hidden,
    titleWordsText: entry.title_words.join(", "),
    sort: entry.sort,
  };
}

/** A new profile starts offered, after the others, with no code work. */
export function newProfileDraft(catalog: readonly ProfileResponse[]): ProfileDraft {
  const last = catalog.reduce((max, entry) => Math.max(max, entry.sort), 0);
  return { label: "", codeWork: false, offered: true, titleWordsText: "", sort: last + 10 };
}

/** The words a title is matched on: lower case, each once, in order. */
export function titleWords(text: string): string[] {
  const words = text
    .split(",")
    .map((word) => word.trim().replace(/\s+/g, " ").toLowerCase())
    .filter(Boolean);
  return [...new Set(words)];
}

export function profileBody(draft: ProfileDraft): ProfileUpdateRequest {
  return {
    label: draft.label.trim().replace(/\s+/g, " "),
    code_work: draft.codeWork,
    hidden: !draft.offered,
    title_words: titleWords(draft.titleWordsText),
    sort: draft.sort,
  };
}

/** Whether the draft says anything the stored entry does not. */
export function profileChanged(entry: ProfileResponse, draft: ProfileDraft): boolean {
  const body = profileBody(draft);
  return (
    body.label !== entry.label ||
    body.code_work !== entry.code_work ||
    body.hidden !== entry.hidden ||
    (body.title_words ?? []).join("|") !== entry.title_words.join("|")
  );
}

/** "Site reliability" as "site_reliability": the id a new profile is kept under. */
export function slugFromLabel(label: string): string {
  const slug = label
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/^[0-9_]+/, "");
  return slug.slice(0, 32);
}

/**
 * What stops a save, in plain words. `slug` is the entry's id; `isNew` adds the checks for
 * one that does not exist yet.
 */
export function profileProblems(
  draft: ProfileDraft,
  slug: string,
  catalog: readonly ProfileResponse[],
  isNew: boolean,
): string[] {
  const problems: string[] = [];
  const label = draft.label.trim();
  if (!label || label.length > MAX_LABEL) {
    problems.push(`Give it a name of 1 to ${MAX_LABEL} characters.`);
  }
  const words = titleWords(draft.titleWordsText);
  if (words.length > MAX_TITLE_WORDS || words.some((word) => word.length > MAX_TITLE_WORD)) {
    problems.push(
      `Name at most ${MAX_TITLE_WORDS} title words, each at most ${MAX_TITLE_WORD} characters.`,
    );
  }
  if (isNew) {
    if (!SLUG.test(slug)) {
      problems.push("Its id is lower-case letters, digits or underscores, starting with a letter.");
    } else if (catalog.some((entry) => entry.slug === slug)) {
      problems.push(`There is a profile ${slug} already. Change that one instead.`);
    }
    if (catalog.length >= MAX_PROFILES) {
      problems.push(`A tenant keeps at most ${MAX_PROFILES} profiles. Hide one you don't use.`);
    }
  }
  const othersOffered = catalog.some((entry) => entry.slug !== slug && !entry.hidden);
  if (!draft.offered && !othersOffered) problems.push("Keep at least one profile offered.");
  if (
    catalog.some(
      (entry) => entry.slug !== slug && entry.label.toLowerCase() === label.toLowerCase(),
    )
  ) {
    problems.push(`Another profile is already called ${label}.`);
  }
  return problems;
}

/** The toast once saved. */
export function profileSaved(label: string, isNew: boolean, offered: boolean): string {
  if (isNew) return `${label} added. It is offered wherever a profile is picked.`;
  return offered
    ? `${label} saved. Pickers show it from now on.`
    : `${label} hidden. People who hold it keep it; nobody can pick it.`;
}
