import type { VerdictCountsDto, VerdictFilter } from "../../api/schema";
import { cn } from "../../lib/utils";
import type { BadgeTone } from "../../lib/status";
import { verdictTiles } from "./portfolioWords";

const count: Record<BadgeTone, string> = {
  success: "text-rag-green",
  warning: "text-rag-amber",
  danger: "text-rag-red",
  info: "text-rag-info",
  neutral: "text-grey-body",
};

/**
 * The count tiles, which are also the filters: a tile picked keeps only its
 * projects; picked again, it lets them go. The counts are over every project
 * the search matches, whatever is picked, so a tile never reads 0 because
 * another one is on.
 */
export function PortfolioTiles({
  counts,
  picked,
  onToggle,
  onClear,
}: {
  counts: VerdictCountsDto;
  picked: readonly VerdictFilter[];
  onToggle: (filter: VerdictFilter) => void;
  onClear: () => void;
}) {
  const tiles = verdictTiles(counts);
  return (
    <div className="mb-6 grid gap-3">
      <div
        role="group"
        aria-label="Filter by the date's answer"
        className="grid grid-cols-4 gap-3 max-md:grid-cols-2"
      >
        {tiles.map((tile) => {
          const on = picked.includes(tile.filter);
          return (
            <button
              key={tile.filter}
              type="button"
              aria-pressed={on}
              onClick={() => onToggle(tile.filter)}
              className={cn(
                "flex min-w-0 flex-col items-start gap-1 rounded-2xl border px-4 py-3 text-left",
                on
                  ? "border-ink bg-grey-fill"
                  : "border-grey-border bg-(--op-viz-surface) hover:bg-grey-fill",
              )}
            >
              <span className={cn("text-[26px] font-extrabold tabular-nums", count[tile.tone])}>
                {tile.count}
              </span>
              <span className="text-[13px] font-bold text-ink">{tile.label}</span>
            </button>
          );
        })}
      </div>
      {picked.length > 0 ? (
        <button
          type="button"
          onClick={onClear}
          className="w-fit text-[13px] font-bold text-ink underline"
        >
          Show every project
        </button>
      ) : null}
    </div>
  );
}
