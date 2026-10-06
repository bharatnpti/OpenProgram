// What the ⌘K palette offers and how a query picks from it. Type imports only,
// so `node --test` can run it.
import type { DirectoryItemResponse } from "../../api/schema";

export type PaletteKind = "screen" | "program" | "project" | "workstream" | "pod" | "person";

export type PaletteRow = {
  key: string;
  kind: PaletteKind;
  label: string;
  /** What it is and where it sits, e.g. "Pod · Checkout Revamp". */
  hint: string;
  to: string;
};

export type Directory = {
  programs: DirectoryItemResponse[];
  projects: DirectoryItemResponse[];
  workstreams: DirectoryItemResponse[];
  pods: DirectoryItemResponse[];
};

/** A person the palette can name: only ever one the API or the directory names. */
export type NamedPerson = { id: string; name: string; title?: string | null };

/** The screens this role is offered, in the navigation's order. */
export function screenRows(screens: { to: string; label: string; hint: string }[]): PaletteRow[] {
  return screens.map((screen) => ({
    key: `screen:${screen.to}`,
    kind: "screen",
    label: screen.label,
    hint: screen.hint,
    to: screen.to,
  }));
}

const delivery = (kind: string, id: string) => `/delivery/${kind}/${encodeURIComponent(id)}`;

function namesOf(ids: string[], items: DirectoryItemResponse[]): string {
  return ids
    .map((id) => items.find((item) => item.id === id)?.name)
    .filter((name): name is string => Boolean(name))
    .join(", ");
}

function hint(kind: string, where: string): string {
  return where ? `${kind} · ${where}` : kind;
}

/**
 * Programs, projects, workstreams and pods, each opening its Delivery panel.
 * Workstreams are optional, so one that holds no work is left out, as every
 * persona view does.
 */
export function directoryRows(directory: Directory): PaletteRow[] {
  const { programs, projects, workstreams, pods } = directory;
  return [
    ...programs.map((item) => ({ item, kind: "program" as const, where: "" })),
    ...projects.map((item) => ({
      item,
      kind: "project" as const,
      where: namesOf(item.program_ids, programs),
    })),
    ...workstreams
      .filter((item) => item.in_use !== false)
      .map((item) => ({
        item,
        kind: "workstream" as const,
        where: namesOf(item.project_ids, projects),
      })),
    ...pods.map((item) => ({
      item,
      kind: "pod" as const,
      where: namesOf(item.project_ids, projects),
    })),
  ].map(({ item, kind, where }) => ({
    key: `${kind}:${item.id}`,
    kind,
    label: item.name,
    hint: hint(kind.charAt(0).toUpperCase() + kind.slice(1), where),
    to: delivery(kind, item.id),
  }));
}

/**
 * People, each opening the pod they work in (their check-in and blockers are
 * there). Someone in no pod has no place in Delivery, so isn't offered; an id
 * nobody names is never offered as a name.
 */
export function peopleRows(people: NamedPerson[], pods: DirectoryItemResponse[]): PaletteRow[] {
  const seen = new Set<string>();
  const rows: PaletteRow[] = [];
  for (const person of people) {
    if (seen.has(person.id) || !person.name || person.name === person.id) continue;
    seen.add(person.id);
    const theirs = pods
      .filter((pod) => pod.member_ids.includes(person.id))
      .sort((a, b) => a.name.localeCompare(b.name));
    if (theirs.length === 0) continue;
    const more = theirs.length > 1 ? ` and ${theirs.length - 1} more` : "";
    const title = person.title?.trim();
    rows.push({
      key: `person:${person.id}`,
      kind: "person",
      label: person.name,
      hint: `${title ? `${title} · ` : "Person · "}${theirs[0].name}${more}`,
      to: delivery("pod", theirs[0].id),
    });
  }
  return rows.sort((a, b) => a.label.localeCompare(b.label));
}

/**
 * The rows a query picks, best first: every word of the query must appear in
 * the row's name or hint; a name that starts with the query beats one with a
 * word that does, which beats a match anywhere else. Ties keep the given
 * order (screens first). No query lists the first rows as given.
 */
export function matchRows(rows: PaletteRow[], query: string, limit = 12): PaletteRow[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return rows.slice(0, limit);
  const words = needle.split(/\s+/);
  const score = (row: PaletteRow) => {
    const label = row.label.toLowerCase();
    if (label.startsWith(needle)) return 0;
    if (label.split(/[\s&·,/-]+/).some((word) => word.startsWith(words[0]))) return 1;
    if (label.includes(needle)) return 2;
    return 3;
  };
  return rows
    .map((row, index) => ({ row, index, text: `${row.label} ${row.hint}`.toLowerCase() }))
    .filter(({ text }) => words.every((word) => text.includes(word)))
    .map((entry) => ({ ...entry, score: score(entry.row) }))
    .sort((a, b) => a.score - b.score || a.index - b.index)
    .slice(0, limit)
    .map(({ row }) => row);
}
