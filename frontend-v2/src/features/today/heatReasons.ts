import type { AttentionLinkDto, PortfolioHeatmapResponse, Rag } from "../../api/schema";

// Only type imports here: this module runs under `node --test` as written,
// which strips types but resolves no extensionless runtime import.

type Cell = Pick<PortfolioHeatmapResponse["cells"][number], "rag" | "reason" | "reasons"> & {
  entity_ref: { kind: string; id: string };
};

/** What a heat tile says under its colour, and what its tooltip lists. */
export type TileReason = { reason: string; tooltip: string };

/**
 * Each heat-map cell's reason, by node kind and id, for the tiles to look up.
 *
 * The reasons are the backend's (``attention.cell_reasons``), so a tile, the
 * headline and the signals name the same drivers. A cell from an older
 * backend carries none, and its tile shows only its colour.
 */
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

export function tileKey(kind: string, id: string): string {
  return `${kind}:${id}`;
}

/** The tooltip: the colour, then every reason, one per line. */
export function tooltipText(rag: Rag, reason: string, reasons: string[]): string {
  const colour = rag === "unknown" ? "No status" : `${rag.charAt(0).toUpperCase()}${rag.slice(1)}`;
  const lines = reasons.length > 0 ? reasons : [reason];
  return [colour, ...lines.map((line) => `• ${line}`)].join("\n");
}

/** Where a signal leads: the delivery page of its node, else the Signals list. */
export function signalHref(link: AttentionLinkDto): string {
  if (link.kind === "signals" || !link.id) return "/signals";
  return `/delivery/${link.kind}/${encodeURIComponent(link.id)}`;
}

/** How long a signal has stood: "open 3d", or "new" for one that started on the day viewed. */
export function signalAge(ageDays: number): string {
  return ageDays > 0 ? `open ${ageDays}d` : "new";
}
