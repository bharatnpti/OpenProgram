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
} {
  const reported = (points ?? []).filter((p) => p.rag !== "unknown");
  const values = (points ?? []).map((p) => (p.rag === "unknown" ? null : (p.score - 1) / 2));
  if (reported.length < 2) return { label: "not enough reported days", values };
  const first = reported[0].score;
  const last = reported[reported.length - 1];
  const direction = last.score > first ? "improving" : last.score < first ? "sliding" : "steady";
  return { label: `${direction}, now ${last.rag}`, values };
}
