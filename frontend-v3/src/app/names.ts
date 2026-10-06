// Only type imports here: this module runs under `node --test` as written,
// which strips types but resolves no extensionless runtime import.
import type { DirectoryItemResponse } from "../api/schema";

/*
 * Who an id is, for the screens that show a person the API names only by id:
 * a drift finding's owner, whoever raised a request, an escalation matrix's
 * decision owner and named levels.
 *
 * No single read names everyone to every role, so the names come from what each
 * role may read (see `useNames` in directory.ts), merged here. An id nobody
 * names stays unnamed: never a guessed name.
 */

/** One person: the id they go by, their name, and any other ids that mean them. */
export type NameEntry = { id: string; name: string; aliases?: string[] };

type NodeLike = {
  id: string;
  kind: string;
  name: string;
  metadata?: Record<string, unknown> | null;
};

/** The metadata key holding a member's chat (Slack, Teams) user id. */
const CHAT_ID_KEY = "chat_external_id";

function chatId(metadata: NodeLike["metadata"]): string[] {
  const value = metadata?.[CHAT_ID_KEY];
  return typeof value === "string" && value.trim() ? [value.trim()] : [];
}

/** The members among graph or config nodes: by node id, and by chat id (people type either). */
export function fromNodes(nodes: NodeLike[] | undefined): NameEntry[] {
  return (nodes ?? [])
    .filter((node) => node.kind === "developer" && node.name.trim())
    .map((node) => ({ id: node.id, name: node.name, aliases: chatId(node.metadata) }));
}

/** The people the heat map names: every member's own cell carries their name. */
export function fromHeatmapCells(
  cells: { entity_ref: { kind: string; id: string }; name?: string | null }[] | undefined,
): NameEntry[] {
  return (cells ?? [])
    .filter((cell) => cell.entity_ref.kind === "developer" && cell.name?.trim())
    .map((cell) => ({ id: cell.entity_ref.id, name: cell.name as string }));
}

/** The people the directory names on pods, projects and workstreams (owner, TPM, scrum master). */
export function fromDirectory(items: DirectoryItemResponse[] | undefined): NameEntry[] {
  const entries: NameEntry[] = [];
  for (const item of items ?? []) {
    for (const person of item.people) {
      if (!person.name) continue;
      entries.push({
        id: person.id,
        name: person.name,
        aliases: person.member_id ? [person.member_id] : [],
      });
    }
  }
  return entries;
}

/** Everyone the acting-as roster of a local tenant lists. */
export function fromRoster(people: { id: string; name: string }[] | undefined): NameEntry[] {
  return (people ?? []).map((person) => ({ id: person.id, name: person.name }));
}

/**
 * One index over every source, the first source to name an id keeping it, so
 * a list ordered most-trusted first never has a later guess override it.
 */
export function indexNames(...sources: NameEntry[][]): Map<string, string> {
  const index = new Map<string, string>();
  for (const source of sources) {
    for (const entry of source) {
      for (const id of [entry.id, ...(entry.aliases ?? [])]) {
        if (id && !index.has(id)) index.set(id, entry.name);
      }
    }
  }
  return index;
}

/** The lookup screens call: `names(id)` is the name, or the id itself when nobody names it. */
export type NameOf = ((id: string | null | undefined) => string) & {
  /** Whether anything names this id. */
  known: (id: string | null | undefined) => boolean;
  /**
   * The name; or, when nobody names the id, the id after a plain word for what
   * it is ("Someone (U123)"): the id stays on screen and no name is
   * guessed. With no id at all, just the word.
   */
  or: (id: string | null | undefined, unnamed: string) => string;
};

export function nameLookup(index: ReadonlyMap<string, string>): NameOf {
  const lookup = ((id) => (id ? (index.get(id) ?? id) : "—")) as NameOf;
  lookup.known = (id) => Boolean(id && index.has(id));
  lookup.or = (id, unnamed) => (id ? (index.get(id) ?? `${unnamed} (${id})`) : unnamed);
  return lookup;
}
