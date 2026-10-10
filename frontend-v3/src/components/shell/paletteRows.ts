// What the ⌘K palette offers and how a query picks from it. Type imports, and
// runtime imports by their .ts path, so `node --test` can run it.
import type { DeliveryTreeResponse, DirectoryItemResponse, Rag } from "../../api/schema";
import type { PaletteTargets } from "../../app/access";
import { ownProjects } from "../../features/delivery/ownTree.ts";
import { ragWords, toneForRag, type BadgeTone } from "../../lib/status.ts";

export type PaletteKind =
  "screen" | "ask" | "program" | "project" | "workstream" | "pod" | "person";

export type PaletteRow = {
  key: string;
  kind: PaletteKind;
  label: string;
  /** What it is and where it sits, e.g. "Pod · Checkout Revamp". */
  hint: string;
  to: string;
  /**
   * A program's, project's, workstream's or pod's status: the directory's own
   * `rag`, as the Delivery navigator draws it. Null when nobody reported one.
   */
  rag?: Rag | null;
  /**
   * The role reads no colour for it (a developer's project): no dot, as the
   * Delivery navigator draws none, rather than a grey one that says "unknown".
   */
  noStatus?: boolean;
  /**
   * A person's pods, the one the row opens first, and what the hint says
   * before them ("Backend Engineer · "). A query naming another of their pods
   * finds them and opens that one.
   */
  person?: { lead: string; pods: { id: string; name: string }[]; toPod: (podId: string) => string };
  /** An assistant row opens the assistant instead of a page, with this in its question field. */
  ask?: { draft: string };
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

/**
 * The assistant's row: `label` (ASK_LABEL in features/assistant/persona.ts)
 * with no query, or for a query whose words are all in it; for any other
 * query, "<label>: “…”", which opens the assistant with the query in its
 * question field, to send or change.
 */
export function askRow(query: string, label: string): PaletteRow {
  const typed = query.trim();
  const words = typed.toLowerCase().split(/\s+/).filter(Boolean);
  const named = words.every((word) => label.toLowerCase().includes(word));
  if (!typed || named) {
    return {
      key: "ask",
      kind: "ask",
      label,
      hint: "A question about the delivery data",
      to: "",
      ask: { draft: "" },
    };
  }
  return {
    key: "ask:query",
    kind: "ask",
    label: `${label}: “${typed}”`,
    hint: "Opens with your question, to send",
    to: "",
    ask: { draft: typed },
  };
}

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
 * Programs, projects, workstreams and pods, each opening where the role has it
 * (`targets`, from the access map): its Delivery panel, or for a role without
 * Delivery its Today or its reports. A kind the role gets no rows of is left
 * out. Workstreams are optional, so one that holds no work is left out, as
 * every persona view does.
 */
export function directoryRows(directory: Directory, targets: PaletteTargets): PaletteRow[] {
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
  ].flatMap(({ item, kind, where }) => {
    const to = targets[kind];
    return to
      ? [
          {
            key: `${kind}:${item.id}`,
            kind,
            label: item.name,
            hint: hint(kind.charAt(0).toUpperCase() + kind.slice(1), where),
            to: to(item.id),
            rag: item.rag ?? null,
          },
        ]
      : [];
  });
}

/**
 * The person's own part of the tree, for a Delivery that lists only it (the
 * `own` scope): their projects, and the pods under them that open, each where
 * Delivery opens it, with the colour its navigator draws. A pod listed by
 * name only opens nothing, so is not offered; a project whose colour the
 * person does not read (a developer's) gets no dot.
 */
export function ownRows(tree: DeliveryTreeResponse, targets: PaletteTargets): PaletteRow[] {
  const projects = ownProjects(tree);
  const { project: toProject, pod: toPod } = targets;
  const programName = (ids: string[]) =>
    ids.map((id) => tree.programs.find((p) => p.id === id)?.name).find(Boolean) ?? "";
  const rows: PaletteRow[] = [];
  if (toProject) {
    for (const project of projects) {
      rows.push({
        key: `project:${project.id}`,
        kind: "project",
        label: project.name,
        hint: hint("Project", programName(project.programIds)),
        to: toProject(project.id),
        rag: project.rag,
        ...(project.rag === null ? { noStatus: true } : {}),
      });
    }
  }
  if (toPod) {
    const seen = new Set<string>();
    for (const pod of projects.flatMap((project) => project.pods)) {
      if (!pod.opens || seen.has(pod.id)) continue;
      seen.add(pod.id);
      const where = projects.filter((p) => p.pods.some((item) => item.id === pod.id));
      rows.push({
        key: `pod:${pod.id}`,
        kind: "pod",
        label: pod.name,
        hint: hint("Pod", where.map((p) => p.name).join(", ")),
        to: toPod(pod.id),
        rag: pod.rag,
      });
    }
  }
  return rows;
}

/** What a row's mark says: a node's status in a colour and words, or what kind of row it is. */
export type PaletteMark =
  | { kind: "status"; tone: BadgeTone; words: string }
  | { kind: "screen" | "ask" | "person" | "plain" };

/**
 * The mark before a row. A program, project, workstream or pod shows its
 * status: the directory's `rag`, the field the Delivery navigator's dot reads,
 * from the same reads (`["directory", kind]`, or the person's own tree under
 * the `own` scope), so the palette and Delivery can never disagree. Green,
 * amber and red say a verdict, grey says nothing is known, and the words go
 * with the colour ("At risk"). A node whose colour the role does not read is
 * `plain`, an empty mark, as the navigator draws no dot for it. A screen, the
 * assistant and a person have no status, so their marks use no status colour.
 */
export function paletteMark(row: Pick<PaletteRow, "kind" | "rag" | "noStatus">): PaletteMark {
  if (row.kind === "screen" || row.kind === "ask" || row.kind === "person") {
    return { kind: row.kind };
  }
  if (row.noStatus) return { kind: "plain" };
  return { kind: "status", tone: toneForRag(row.rag), words: ragWords(row.rag) };
}

/**
 * People, each opening the pod they work in (their check-in and blockers are
 * there), for the roles that read a pod's people (`toPod`; none for the rest).
 * Someone in no pod has no place to open, so isn't offered; an id nobody names
 * is never offered as a name.
 */
export function peopleRows(
  people: NamedPerson[],
  pods: DirectoryItemResponse[],
  toPod: ((podId: string) => string) | null,
): PaletteRow[] {
  if (!toPod) return [];
  const seen = new Set<string>();
  const rows: PaletteRow[] = [];
  for (const person of people) {
    if (seen.has(person.id) || !person.name || person.name === person.id) continue;
    seen.add(person.id);
    const theirs = pods
      .filter((pod) => pod.member_ids.includes(person.id))
      .sort((a, b) => a.name.localeCompare(b.name));
    if (theirs.length === 0) continue;
    const title = person.title?.trim();
    const lead = title ? `${title} · ` : "Person · ";
    rows.push({
      key: `person:${person.id}`,
      kind: "person",
      label: person.name,
      hint: personHint(lead, theirs),
      to: toPod(theirs[0].id),
      person: { lead, pods: theirs.map((pod) => ({ id: pod.id, name: pod.name })), toPod },
    });
  }
  return rows.sort((a, b) => a.label.localeCompare(b.label));
}

/** "Backend Engineer · Payments Pod and 1 more": the first pod named, the others counted. */
function personHint(lead: string, pods: { name: string }[]): string {
  const more = pods.length > 1 ? ` and ${pods.length - 1} more` : "";
  return `${lead}${pods[0].name}${more}`;
}

/**
 * People the palette cannot take anywhere, because they are in no pod, whose
 * name a query picks out. An empty result then says why, instead of reading as
 * "no such person".
 */
export function noPodNote(
  people: NamedPerson[],
  pods: DirectoryItemResponse[],
  query: string,
): string | null {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return null;
  const seen = new Set<string>();
  const lonely = people.filter((person) => {
    if (seen.has(person.id) || !person.name || person.name === person.id) return false;
    seen.add(person.id);
    const name = person.name.toLowerCase();
    return (
      words.every((word) => name.includes(word)) &&
      !pods.some((pod) => pod.member_ids.includes(person.id))
    );
  });
  if (lonely.length === 0) return null;
  const names = lonely.map((person) => person.name).join(", ");
  return `${names} ${lonely.length === 1 ? "is" : "are"} in no pod, so there is no pod page to open.`;
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
  // A person's row also answers to every pod they are in, not only the one it names.
  const text = (row: PaletteRow) =>
    [row.label, row.hint, ...(row.person?.pods.map((pod) => pod.name) ?? [])]
      .join(" ")
      .toLowerCase();
  return rows
    .map((row, index) => ({ row, index, text: text(row) }))
    .filter((entry) => words.every((word) => entry.text.includes(word)))
    .map((entry) => ({ ...entry, row: focusPod(entry.row, words), score: score(entry.row) }))
    .sort((a, b) => a.score - b.score || a.index - b.index)
    .slice(0, limit)
    .map(({ row }) => row);
}

/**
 * A person found by one of their other pods opens that pod and names it, so the
 * row says why it matched ("pay" finds someone whose first pod is Identity).
 */
function focusPod(row: PaletteRow, words: string[]): PaletteRow {
  const person = row.person;
  if (!person || person.pods.length < 2) return row;
  const named = (pod: { name: string }) =>
    words.some((word) => pod.name.toLowerCase().includes(word));
  // Already first and named, or nothing of theirs named at all: as it was.
  if (named(person.pods[0])) return row;
  const hit = person.pods.find(named);
  if (!hit) return row;
  const pods = [hit, ...person.pods.filter((pod) => pod.id !== hit.id)];
  return { ...row, hint: personHint(person.lead, pods), to: person.toPod(hit.id) };
}
