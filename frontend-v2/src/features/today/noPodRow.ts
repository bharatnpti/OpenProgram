import type { PortfolioHeatmapResponse, Rag, StatusSource } from "../../api/schema";

// Only type imports here: this module runs under `node --test` as written,
// which strips types but resolves no extensionless runtime import.

type Cell = Pick<
  PortfolioHeatmapResponse["cells"][number],
  "row" | "rag" | "source" | "why" | "name"
> & { entity_ref: { id: string } };

/** The heat map's row for people in no team (N5), as the backend names it. */
export const NO_POD_ROW = "no pod";

export type NoPodTile = { id: string; name: string; rag: Rag; state: string; why: string };

// Worst first, as the team rows sort; silence is not a clean bill of health.
const SEVERITY: Record<Rag, number> = { red: 3, amber: 2, unknown: 1, green: 0 };

// A check-in state as the tile says it: what the person's update was.
const STATE: Record<StatusSource, string> = {
  confirmed: "confirmed",
  partial: "partial",
  stale: "stale",
  inferred: "inferred",
  unknown: "no status",
};

/**
 * The people in no team -- an exec, or someone not yet placed in a pod -- each
 * with their own check-in, worst first, at most `limit` of `total`.
 *
 * No pod, project or program counts these cells, so they get a row of their
 * own and never feed the headline. A person is named, never shown by an id.
 */
export function noPodTiles(
  cells: Cell[] | undefined,
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
    }))
    .sort((a, b) => SEVERITY[b.rag] - SEVERITY[a.rag] || a.name.localeCompare(b.name));
  return { tiles: people.slice(0, limit), total: people.length };
}
