// Only type imports, and runtime imports by their .ts path: this module runs
// under `node --test` as written.
import type { PortfolioHeatmapResponse, Rag, StatusSource } from "../../api/schema";
import { REPLY_WORDS } from "../../lib/checkinWords.ts";

/*
 * The portfolio heat map, on All projects › Early warnings: today's health of
 * every project, workstream and pod of a programme, worst first, and the people
 * in no pod. It is today's health, not the date: the one place a health colour
 * fills a tile.
 *
 * The heat map's cells carry, for each node, a few words that say why it has
 * its colour (`reason`) and every reason behind it (`reasons`). The words are
 * the backend's (`attention.cell_reasons`), so a tile, the programme's verdict
 * above it and the warnings beside it name the same drivers. A cell from an
 * older backend carries none, and its tile then shows only its colour.
 */

type Cell = Pick<PortfolioHeatmapResponse["cells"][number], "rag" | "reason" | "reasons"> & {
  entity_ref: { kind: string; id: string };
};

/** What a heat tile says under its colour, and what its tooltip lists. */
export type TileReason = { reason: string; tooltip: string };

export function tileKey(kind: string, id: string): string {
  return `${kind}:${id}`;
}

/** Each cell's reason, by node kind and id, for the tiles to look up. */
export function tileReasons(cells: Cell[] | undefined): Map<string, TileReason> {
  const reasons = new Map<string, TileReason>();
  (cells ?? []).forEach((cell) => {
    if (!cell.reason) return;
    reasons.set(tileKey(cell.entity_ref.kind, cell.entity_ref.id), {
      reason: cell.reason,
      tooltip: tooltipText(cell.rag, cell.reason, cell.reasons ?? []),
    });
  });
  return reasons;
}

/**
 * Each cell's colour, by node kind and id. A tile takes its colour from the
 * heat map cell that gives its reason, not from the directory's stored status:
 * before the first rollup the directory has none, while the heat map works it
 * out, and a grey tile would sit under a red tooltip.
 */
export function tileColours(cells: Cell[] | undefined): Map<string, Rag> {
  return new Map(
    (cells ?? []).map((cell) => [tileKey(cell.entity_ref.kind, cell.entity_ref.id), cell.rag]),
  );
}

/**
 * How many reasons each cell gives, by node kind and id. Among tiles of one
 * colour the one with more reasons is listed first: it has more wrong with it.
 */
export function tileWeights(cells: Cell[] | undefined): Map<string, number> {
  return new Map(
    (cells ?? []).map((cell) => [
      tileKey(cell.entity_ref.kind, cell.entity_ref.id),
      cell.reasons?.length ?? (cell.reason ? 1 : 0),
    ]),
  );
}

const SEVERITY: Record<Rag, number> = { red: 3, amber: 2, unknown: 1, green: 0 };

/**
 * A row's nodes worst first: worst colour, then the one with more reasons,
 * then A to Z.
 */
export function rankByHeat<T extends { id: string; name: string }>(
  items: readonly T[],
  colourOf: (item: T) => Rag,
  weightOf: (item: T) => number,
): T[] {
  return [...items].sort(
    (a, b) =>
      SEVERITY[colourOf(b)] - SEVERITY[colourOf(a)] ||
      weightOf(b) - weightOf(a) ||
      a.name.localeCompare(b.name),
  );
}

/** The most tiles a heat row grows to, when ties at its edge would otherwise hide some. */
export const HEAT_CAP = 8;

/**
 * The tiles a heat row shows, from the nodes ranked worst first: the first
 * `columns`, and more when the row's last tile is not green and the next ones
 * are just as bad. Ties are listed A to Z, so cutting them off hid a pod that
 * may be the worst of them ("worst 4 of 5" with all five amber); up to `cap`
 * tiles are shown instead, and a green tie is not worth the room.
 */
export function heatTiles<T>(
  ranked: readonly T[],
  colourOf: (item: T) => Rag,
  columns: number,
  cap: number = HEAT_CAP,
): { shown: T[]; hidden: number; hiddenAsBad: boolean } {
  let end = Math.min(columns, ranked.length);
  const edge = end > 0 ? colourOf(ranked[end - 1]) : null;
  if (edge !== null && edge !== "green") {
    while (end < ranked.length && end < cap && colourOf(ranked[end]) === edge) end += 1;
  }
  return {
    shown: ranked.slice(0, end),
    hidden: ranked.length - end,
    hiddenAsBad:
      end < ranked.length && edge !== null && edge !== "green" && colourOf(ranked[end]) === edge,
  };
}

/** A colour in words, for a tile and its tooltip: "Red", "No status". */
export function colourWord(rag: Rag): string {
  return rag === "unknown" ? "No status" : `${rag.charAt(0).toUpperCase()}${rag.slice(1)}`;
}

/**
 * A node's health today in a sentence, in colour words, never the date's
 * ("on track" is the date verdict's): "Checkout Revamp is red today."
 */
export function healthSentence(name: string, rag: Rag): string {
  return rag === "unknown" ? `${name} has no status today.` : `${name} is ${rag} today.`;
}

/** The tooltip: the colour, then every reason, one per line. */
export function tooltipText(rag: Rag, reason: string, reasons: string[]): string {
  const lines = reasons.length > 0 ? reasons : [reason];
  return [colourWord(rag), ...lines.map((line) => `• ${line}`)].join("\n");
}

/** The programme's own cell on the heat map: its colour, and the few words that say why. */
export function programCell(
  cells: Cell[] | undefined,
  programId: string,
): { rag: Rag; reason: string | null } | null {
  const cell = (cells ?? []).find(
    (item) => item.entity_ref.kind === "program" && item.entity_ref.id === programId,
  );
  return cell ? { rag: cell.rag, reason: cell.reason?.trim() || null } : null;
}

/** What the programme's health leads with. */
export type ProgrammeVerdict = {
  rag: Rag;
  headline: string;
  detail: string | null;
  /** The programme's own colour is worse than its teams', so it leads. */
  fromProgram: boolean;
};

const capital = (word: string) => `${word.charAt(0).toUpperCase()}${word.slice(1)}`;

/**
 * The programme's health headline. The backend colours it by the worst of the
 * programme's pods, projects and workstreams, and leaves the programme's own
 * status out (attention.py `attention_view`). A programme is red when two
 * different blockers are open under it, though every team is amber, so the
 * headline said "Amber" above a 30-day line that said "now red".
 *
 * When the programme's own colour is worse (red or amber over a better
 * headline), it leads, with the first reason the heat map gives it, and what
 * the teams read follows in the second line. Otherwise the server's verdict
 * stands as it is.
 */
export function programmeVerdict(
  attention: { rag: Rag; headline: string; detail?: string | null },
  program: { rag: Rag; reason: string | null } | null,
): ProgrammeVerdict {
  const own = {
    rag: attention.rag,
    headline: attention.headline,
    detail: attention.detail ?? null,
    fromProgram: false,
  };
  if (!program || (program.rag !== "red" && program.rag !== "amber")) return own;
  if (SEVERITY[program.rag] <= SEVERITY[attention.rag]) return own;
  const lead = program.reason
    ? `${capital(program.rag)}: ${program.reason}.`
    : `${capital(program.rag)}: the programme's own status is ${program.rag}. Every reason is below.`;
  const teams = `Teams read ${attention.headline}`;
  return {
    rag: program.rag,
    headline: lead,
    detail: attention.detail ? `${teams} ${attention.detail}` : teams,
    fromProgram: true,
  };
}

/** The heat map's row for people in no team, as the backend names it. */
export const NO_POD_ROW = "no pod";

export type NoPodTile = {
  id: string;
  name: string;
  rag: Rag;
  /** The person's check-in state in words: "replied", "partly replied", "no status". */
  state: string;
  why: string;
  /** The backend's short reason, e.g. "Check-in unanswered today"; null from an older one. */
  reason: string | null;
  /** Every reason, one per line, for the tooltip; the cell's why when it has none. */
  reasons: string[];
};

const STATE: Record<StatusSource, string> = {
  confirmed: REPLY_WORDS.replied,
  partial: REPLY_WORDS.partly,
  stale: REPLY_WORDS.carried,
  inferred: REPLY_WORDS.inferred,
  unknown: "no status",
};

type NoPodCell = Pick<
  PortfolioHeatmapResponse["cells"][number],
  "row" | "rag" | "source" | "why" | "name"
> &
  Partial<Pick<PortfolioHeatmapResponse["cells"][number], "reason" | "reasons">> & {
    entity_ref: { id: string };
  };

/**
 * The people in no team (an executive, or someone not yet placed in a pod),
 * each with their own check-in, worst first, at most `limit` of `total`.
 *
 * No pod, project or programme counts these cells, so they get a row of their
 * own and never feed the verdict. A person is named, never shown by an id.
 */
export function noPodTiles(
  cells: NoPodCell[] | undefined,
  limit: number,
): { tiles: NoPodTile[]; total: number } {
  const people = (cells ?? [])
    .filter((cell) => cell.row === NO_POD_ROW)
    .map((cell) => ({
      id: cell.entity_ref.id,
      name: cell.name ?? "A team member",
      rag: cell.rag,
      state: STATE[cell.source] ?? cell.source,
      why: cell.why,
      reason: cell.reason ?? null,
      reasons: cell.reasons && cell.reasons.length > 0 ? cell.reasons : [cell.why],
    }))
    .sort((a, b) => SEVERITY[b.rag] - SEVERITY[a.rag] || a.name.localeCompare(b.name));
  return { tiles: people.slice(0, limit), total: people.length };
}
