// A workstream's row in words, pure. Type imports, and runtime imports by their
// .ts path, so `node --test` runs it as written.
import type { DirectoryItemResponse } from "../../api/schema";
import { PERSON_KEY_WORDS } from "../../lib/words.ts";

type Workstream = Pick<DirectoryItemResponse, "metadata" | "people">;

const text = (value: unknown): string | null =>
  typeof value === "string" && value.trim() ? value.trim() : null;

/**
 * "Build · phase Delivery · target Mon 30 Nov · Owner Mina Patel · code in
 * acme/checkout-api": what the admin set (type, phase, target date), who runs
 * it (named only for a reader who sees names, and never by an id), and where
 * its code lives. Empty values are left out, so a workstream with none says
 * nothing under its name; its tasks are counted when it is opened.
 */
export function workstreamLine(
  item: Workstream,
  names: boolean,
  formatDay: (iso: string) => string,
): string {
  const target = text(item.metadata.target_date);
  const phase = text(item.metadata.phase);
  const repos = text(item.metadata.github_repos)
    ?.split(/[\s,]+/)
    .filter(Boolean)
    .join(", ");
  const people = names
    ? item.people.flatMap((person) =>
        person.name ? [`${PERSON_KEY_WORDS[person.key] ?? person.key} ${person.name}`] : [],
      )
    : [];
  return [
    text(item.metadata.type),
    phase ? `phase ${phase}` : null,
    target ? `target ${formatDay(target)}` : null,
    ...people,
    repos ? `code in ${repos}` : null,
  ]
    .filter((part): part is string => Boolean(part))
    .join(" · ");
}

/** Workstreams in order of their target date (none last), then by name. */
/**
 * The Workstreams section's line: how many, and what a fold holds for this
 * reader: its tasks and reasons for a reader of project progress, else its
 * facts and pods (the progress read is the progress readers').
 */
export function workstreamsMeta(count: number, readsProgress: boolean): string {
  const many = `${count} ${count === 1 ? "workstream" : "workstreams"}`;
  return readsProgress
    ? `${many} · open one for its tasks and reasons`
    : `${many} · open one for its facts and pods`;
}

export function byTarget<T extends Pick<DirectoryItemResponse, "metadata" | "name">>(
  items: readonly T[],
): T[] {
  const target = (item: T) => text(item.metadata.target_date) ?? "9999-12-31";
  return [...items].sort(
    (a, b) => target(a).localeCompare(target(b)) || a.name.localeCompare(b.name),
  );
}
