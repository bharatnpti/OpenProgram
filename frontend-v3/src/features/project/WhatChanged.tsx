import { useState } from "react";

import type { DeliveryImpactResponse } from "../../api/schema";
import { PanelState } from "../../components/PanelState";
import { cn } from "../../lib/utils";
import type { Row } from "../overall/timeline";
import { VizCard } from "../overall/viz";
import { chipLabel, whatChanged, type ChangeChip } from "./whatChanged.ts";

/** The newest lines shown before "Show N earlier". */
const SHOWN = 8;

/**
 * What changed for the row picked on the timeline, over the period Why the
 * date moved reads (D88): one dated line per event, oldest first, a chip of
 * working days on each scope event and none on evidence, and the check that
 * the chips and team speed make the waterfall's total. The committed date's
 * moves are said under Why the date moved, once.
 */
export function WhatChanged({
  row,
  impact,
  loading,
}: {
  row: Row;
  impact: DeliveryImpactResponse | undefined;
  loading: boolean;
}) {
  const [all, setAll] = useState(false);
  const changed = impact ? whatChanged(impact) : null;
  const lines = changed?.lines ?? [];
  const hidden = all ? 0 : Math.max(0, lines.length - SHOWN);
  const shown = lines.slice(hidden);
  const footer = changed?.footer ?? null;

  return (
    <VizCard aria-label={`What changed: ${row.name}`}>
      <div className="grid gap-1">
        <h3 id="what-changed" className="min-w-0 text-[16px] font-extrabold text-balance">
          What changed · {row.name}
        </h3>
        {impact ? (
          <p className="text-[12.5px] text-grey-secondary">
            {capitalised(impact.period_words)} · the days are working days on the{" "}
            {impact.measure === "p50" ? "likely" : "latest likely"} date, on scope changes only
          </p>
        ) : null}
      </div>
      <PanelState isLoading={loading && !impact} error={null}>
        {lines.length === 0 ? (
          <p className="text-[13px] text-grey-secondary">
            Nothing joined, left or was reopened in this period.
          </p>
        ) : (
          <div className="grid gap-2">
            {hidden > 0 ? (
              <button
                type="button"
                onClick={() => setAll(true)}
                className="h-8 w-fit rounded-full border border-grey-border bg-(--op-viz-surface) px-3 text-[12.5px] font-bold text-grey-body hover:bg-grey-fill max-sm:h-10"
              >
                Show {hidden} earlier {hidden === 1 ? "change" : "changes"}
              </button>
            ) : null}
            <ol className="m-0 grid list-none gap-0 p-0" aria-label="Changes, oldest first">
              {shown.map((line) => (
                <li
                  key={line.key}
                  className="grid grid-cols-[14px_56px_minmax(0,1fr)] items-start gap-x-2.5 gap-y-1 border-t border-(--op-viz-grid) py-2 first:border-t-0 sm:grid-cols-[14px_56px_minmax(0,1fr)_auto]"
                >
                  <span
                    aria-hidden
                    className={cn(
                      "mt-[5px] h-2.5 w-2.5 rounded-full",
                      line.pushed ? "bg-(--op-red)" : "bg-(--op-grey-secondary)",
                    )}
                  />
                  <span className="text-[13px] font-bold text-grey-body tabular-nums">
                    {line.dayLabel}
                  </span>
                  <span className="min-w-0 text-[13.5px] text-ink">
                    {line.text}
                    {line.pushed ? <span className="sr-only">, pushed the date</span> : null}
                    {line.keys ? (
                      <span className="block text-[12px] text-grey-secondary">{line.keys}</span>
                    ) : null}
                  </span>
                  {line.chip ? (
                    <span className="col-start-3 sm:col-start-4 sm:row-start-1 sm:justify-self-end">
                      <Chip chip={line.chip} />
                    </span>
                  ) : null}
                </li>
              ))}
            </ol>
          </div>
        )}
        {footer ? (
          <div className="grid gap-1 rounded-2xl bg-grey-fill px-4 py-3 text-[12.5px] text-grey-body">
            <p className="font-bold text-ink">
              {footer.adds ? "Adds up: " : "Does not add up alone: "}
              {footer.text}.
            </p>
            {footer.adds ? null : (
              <p>
                Re-estimated requirements join a bar of Why the date moved but no event, so the
                chips leave them out.
              </p>
            )}
            {footer.notes.map((note) => (
              <p key={note}>{note}</p>
            ))}
          </div>
        ) : null}
      </PanelState>
    </VizCard>
  );
}

function Chip({ chip }: { chip: ChangeChip }) {
  const tone =
    chip.kind === "unknown"
      ? "border-grey-border text-grey-secondary"
      : chip.value > 0.05
        ? "border-(--op-red) text-(--op-red)"
        : chip.value < -0.05
          ? "border-(--op-green) text-(--op-green)"
          : "border-grey-border text-grey-body";
  return (
    <span
      aria-label={chipLabel(chip)}
      className={cn(
        "inline-flex h-6 items-center gap-1 rounded-full border px-2.5 text-[12px] font-extrabold whitespace-nowrap tabular-nums",
        tone,
      )}
    >
      <span aria-hidden>{chip.text}</span>
      {chip.kind === "days" && chip.reconstructed ? (
        <span aria-hidden className="font-bold text-grey-secondary">
          · reconstructed
        </span>
      ) : null}
    </span>
  );
}

function capitalised(text: string): string {
  return text ? `${text.charAt(0).toUpperCase()}${text.slice(1)}` : text;
}
