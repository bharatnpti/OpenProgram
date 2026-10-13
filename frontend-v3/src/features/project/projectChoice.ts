// Which project the Project page opens, and what its switcher lists (D86): the
// projects the server lets the person open, theirs first, then the ones they
// opened lately in this browser, then the rest by programme, worst first. The
// URL always wins; the default applies only to a bare /projects. Pure, with
// type imports only, so `node --test` runs it as written.
import type { Rag } from "../../api/schema";

export type ProjectOption = {
  id: string;
  name: string;
  /** Null where the person reads no colour for it (a developer's project). */
  rag: Rag | null;
  /** Its programmes, the first one its heading. */
  programIds: string[];
  /** The Jira project key, which a search finds it by too. */
  jiraKey: string | null;
  /** One of the person's own: of their pods, or one they own (`GET /me/delivery-tree`). */
  mine: boolean;
};

export type ProjectSection = {
  /** "mine", "recent", or "program:<id>" ("program:" for projects in none). */
  id: string;
  label: string;
  options: ProjectOption[];
};

/** How many projects "Recent" keeps, newest first. */
export const RECENT_KEPT = 5;

const SEVERITY: Record<Rag, number> = { red: 3, amber: 2, unknown: 1, green: 0 };

/** Worst first: red, amber, unknown (silence is no clean bill of health), green; no colour last. */
export function worstFirst(a: ProjectOption, b: ProjectOption): number {
  const level = (option: ProjectOption) => (option.rag === null ? -1 : SEVERITY[option.rag]);
  return level(b) - level(a) || a.name.localeCompare(b.name);
}

/**
 * The project a bare /projects opens: the worst of the person's own; when none
 * is theirs, the one they opened last (if still listed); else the worst of
 * all. Null when nothing is listed.
 */
export function defaultProject(options: ProjectOption[], recent: readonly string[]): string | null {
  const mine = options.filter((option) => option.mine).sort(worstFirst);
  if (mine[0]) return mine[0].id;
  const last = recent.find((id) => options.some((option) => option.id === id));
  if (last) return last;
  return [...options].sort(worstFirst)[0]?.id ?? null;
}

/** Every word of the query in the name, a programme's name or the Jira key. */
export function matchesQuery(
  option: ProjectOption,
  query: string,
  programName: (id: string) => string | undefined,
): boolean {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return true;
  const text = [
    option.name,
    option.jiraKey ?? "",
    ...option.programIds.map((id) => programName(id) ?? ""),
  ]
    .join(" ")
    .toLowerCase();
  return words.every((word) => text.includes(word));
}

/**
 * The switcher's list: Yours (worst first), Recent (newest first, not yours),
 * then the rest grouped by programme, each group worst first, the programmes in
 * the order given (worst first upstream). Each project is listed once, in the
 * first section that has it, so the arrow keys never meet it twice. A section
 * with nothing is left out; a query narrows every section.
 */
export function switcherSections(
  options: ProjectOption[],
  recent: readonly string[],
  programs: { id: string; name: string }[],
  query = "",
): ProjectSection[] {
  const programName = (id: string) => programs.find((program) => program.id === id)?.name;
  const found = options.filter((option) => matchesQuery(option, query, programName));
  const placed = new Set<string>();
  const take = (list: ProjectOption[]) => {
    const kept = list.filter((option) => !placed.has(option.id));
    for (const option of kept) placed.add(option.id);
    return kept;
  };
  const sections: ProjectSection[] = [];
  const mine = take(found.filter((option) => option.mine).sort(worstFirst));
  if (mine.length > 0) sections.push({ id: "mine", label: "Yours", options: mine });
  const lately = take(
    recent
      .slice(0, RECENT_KEPT)
      .map((id) => found.find((option) => option.id === id))
      .filter((option): option is ProjectOption => option !== undefined),
  );
  if (lately.length > 0) sections.push({ id: "recent", label: "Recent", options: lately });
  const rest = found.filter((option) => !placed.has(option.id));
  for (const program of programs) {
    const group = take(rest.filter((option) => option.programIds[0] === program.id));
    if (group.length > 0) {
      sections.push({
        id: `program:${program.id}`,
        label: program.name,
        options: group.sort(worstFirst),
      });
    }
  }
  const loose = take(rest).sort(worstFirst);
  if (loose.length > 0) {
    sections.push({
      id: "program:",
      label: sections.length > 0 ? "Other projects" : "All projects",
      options: loose,
    });
  }
  return sections;
}

/** The recent list with `id` opened now: first, once, at most RECENT_KEPT. */
export function remembered(recent: readonly string[], id: string): string[] {
  return [id, ...recent.filter((item) => item !== id)].slice(0, RECENT_KEPT);
}

/** The few calls of localStorage these use, so a test can hand in its own. */
export type RecentStore = Pick<Storage, "getItem" | "setItem">;

/** The storage key, per person, so two people on one browser keep their own. */
export function recentKey(who: string | null | undefined): string {
  return `openprogram.v3.recent-projects.${who || "anyone"}`;
}

/**
 * The stored recent list; empty when there is none, it is not a list of ids,
 * or storage is blocked (a private window). A per-viewer convenience only: it
 * is never sent anywhere.
 */
export function readRecent(storage: () => RecentStore, key: string): string[] {
  try {
    const raw = storage().getItem(key);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed)
      ? parsed.filter((item): item is string => typeof item === "string").slice(0, RECENT_KEPT)
      : [];
  } catch {
    return [];
  }
}

/** Keep the list; blocked storage only means it is not remembered. */
export function writeRecent(storage: () => RecentStore, key: string, recent: string[]): void {
  try {
    storage().setItem(key, JSON.stringify(recent.slice(0, RECENT_KEPT)));
  } catch {
    // Private windows and blocked storage: the list simply isn't kept.
  }
}
