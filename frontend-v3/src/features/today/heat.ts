// Only type imports here: this module runs under `node --test` as written,
// which strips types but resolves no extensionless runtime import.
import type {
  AttentionLinkDto,
  NodeTrendResponse,
  PortfolioHeatmapResponse,
  Rag,
  StatusSource,
} from "../../api/schema";

/*
 * Portfolio heat, for the manager, executive and admin Today.
 *
 * The heat map's cells carry, for each node, a few words that say why it has
 * its colour (`reason`) and every reason behind it (`reasons`). The words are
 * the backend's (`attention.cell_reasons`), so a tile, the verdict above it and
 * the signals below it name the same drivers. A cell from an older backend
 * carries none, and its tile then shows only its colour.
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

/** The tooltip: the colour, then every reason, one per line. */
export function tooltipText(rag: Rag, reason: string, reasons: string[]): string {
  const colour = rag === "unknown" ? "No status" : `${rag.charAt(0).toUpperCase()}${rag.slice(1)}`;
  const lines = reasons.length > 0 ? reasons : [reason];
  return [colour, ...lines.map((line) => `• ${line}`)].join("\n");
}

/** The heat map's row for people in no team, as the backend names it. */
export const NO_POD_ROW = "no pod";

export type NoPodTile = {
  id: string;
  name: string;
  rag: Rag;
  /** The person's check-in state in words: "confirmed", "partial", "no status". */
  state: string;
  why: string;
  /** The backend's short reason, e.g. "Check-in unanswered today"; null from an older one. */
  reason: string | null;
  /** Every reason, one per line, for the tooltip; the cell's why when it has none. */
  reasons: string[];
};

const SEVERITY: Record<Rag, number> = { red: 3, amber: 2, unknown: 1, green: 0 };

const STATE: Record<StatusSource, string> = {
  confirmed: "confirmed",
  partial: "partial",
  stale: "stale",
  inferred: "inferred",
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
 * No pod, project or program counts these cells, so they get a row of their
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

/** Where a signal leads: the delivery page of its node, else the Signals list. */
export function signalHref(link: AttentionLinkDto): string {
  if (link.kind === "signals" || !link.id) return "/signals";
  return `/delivery/${link.kind}/${encodeURIComponent(link.id)}`;
}

/**
 * Direction of travel over the reported days only: an `unknown` day carries
 * score 0, below red, so it is not a value on the health scale and comparing
 * it as one would manufacture a direction. Says where it stands now, because
 * a program that is red all month draws a flat line at the bottom.
 */
export function momentum(points: NodeTrendResponse["points"] | undefined): {
  label: string;
  values: (number | null)[];
  /** Days in the window, and how many of them reported a status. */
  days: number;
  reportedDays: number;
} {
  const reported = (points ?? []).filter((p) => p.rag !== "unknown");
  const values = (points ?? []).map((p) => (p.rag === "unknown" ? null : (p.score - 1) / 2));
  const counts = { days: (points ?? []).length, reportedDays: reported.length };
  if (reported.length < 2) return { label: "not enough reported days", values, ...counts };
  const first = reported[0].score;
  const last = reported[reported.length - 1];
  const direction = last.score > first ? "improving" : last.score < first ? "sliding" : "steady";
  return { label: `${direction}, now ${last.rag}`, values, ...counts };
}

/**
 * The line under "Momentum": the window, and how much of it the direction rests
 * on. "30 days" beside a line that is empty for most of them says more than it
 * knows, so the days that reported are counted. The history may hold fewer
 * entries than the window has days (it keeps working days only).
 */
export function momentumNote(
  m: { label: string; days: number; reportedDays: number },
  windowDays = 30,
): string {
  return m.reportedDays > 0 && m.reportedDays < m.days
    ? `${windowDays} days · ${m.reportedDays} ${m.reportedDays === 1 ? "day" : "days"} reported · ${m.label}`
    : `${windowDays} days · ${m.label}`;
}
