import { useState } from "react";

import type { DeliveryImpactResponse, Measure, PeriodKind } from "../../api/schema";
import { PanelState, TableBox, td, th } from "../../components/PanelState";
import { Fold } from "../../components/viz/Fold";
import { cn } from "../../lib/utils";
import { shortDay, type Row } from "./timeline";
import { VizCard } from "./viz";
import {
  PERIODS,
  causeBars,
  committedLine,
  daysWords,
  endsLine,
  measureWords,
  signed,
  teamLine,
  type Bar,
} from "./waterfall";

/**
 * "Why the date moved", for the row picked on the timeline, to be read in five
 * seconds: the server's one sentence (where the likely date
 * stands, then why), at most three causes as bars from zero (red to the right
 * for what pushed it later, green to the left for what pulled it in, the days
 * at the end, each opening its requirements), then the committed date, the
 * team's date and the requirements with no date as short lines. The date shown
 * (at the latest likely, or likely) and a table sit behind Details. Drawn only
 * for a row the reader has whole; the server decides the rest.
 */
export function ImpactWaterfall({
  row,
  read,
  measure,
  onMeasure,
  period,
  planned,
  onPeriod,
  onPlan,
}: {
  row: Row;
  read: {
    data: DeliveryImpactResponse | undefined;
    isLoading: boolean;
    error: unknown;
    refetch: () => unknown;
  };
  measure: Measure;
  onMeasure: (measure: Measure) => void;
  period: PeriodKind;
  planned: boolean;
  onPeriod: (period: PeriodKind) => void;
  onPlan?: () => void;
}) {
  const impact = read.data;
  return (
    <VizCard aria-label={`Why the date moved: ${row.name}`}>
      <div className="grid gap-3">
        <h3 className="min-w-0 text-[16px] font-extrabold text-balance">
          Why the date moved · {row.name}
        </h3>
        <Periods value={period} planned={planned} onChange={onPeriod} onPlan={onPlan} />
      </div>
      <PanelState isLoading={read.isLoading} error={read.error} onRetry={() => void read.refetch()}>
        {impact ? <Explained impact={impact} measure={measure} onMeasure={onMeasure} /> : null}
      </PanelState>
    </VizCard>
  );
}

/**
 * The period chips. Before planning is marked complete the first is "Mark
 * planning complete", for whoever may mark it; for anyone else it is not drawn.
 */
function Periods({
  value,
  planned,
  onChange,
  onPlan,
}: {
  value: PeriodKind;
  planned: boolean;
  onChange: (period: PeriodKind) => void;
  onPlan?: () => void;
}) {
  return (
    <div role="group" aria-label="Period" className="flex flex-wrap gap-1.5">
      {PERIODS.map((period) =>
        period.kind === "since_baseline" && !planned ? (
          onPlan ? (
            <button
              key={period.kind}
              type="button"
              onClick={onPlan}
              className="h-8 rounded-full border border-dashed border-ink px-3 text-[12.5px] font-bold text-ink hover:bg-grey-fill max-sm:h-10"
            >
              Mark planning complete
            </button>
          ) : null
        ) : (
          <button
            key={period.kind}
            type="button"
            aria-pressed={value === period.kind}
            onClick={() => onChange(period.kind)}
            className={cn(
              "h-8 rounded-full border px-3 text-[12.5px] font-bold max-sm:h-10",
              value === period.kind
                ? "border-ink bg-ink text-(--op-viz-surface)"
                : "border-grey-border bg-(--op-viz-surface) text-grey-body hover:bg-grey-fill",
            )}
          >
            {period.label}
          </button>
        ),
      )}
    </div>
  );
}

function Explained({
  impact,
  measure,
  onMeasure,
}: {
  impact: DeliveryImpactResponse;
  measure: Measure;
  onMeasure: (measure: Measure) => void;
}) {
  const bars = causeBars(impact.drivers, measure);
  const ends = endsLine(impact, measure);
  return (
    <div className="grid min-w-0 gap-4">
      <p className="text-[15px] font-bold text-ink text-pretty">{impact.summary}</p>
      {bars.length > 0 ? <Bars bars={bars} /> : <Ledger impact={impact} />}
      <Lanes impact={impact} ends={ends} />
      {impact.not_known.length > 0 ? (
        <p className="text-[12px] text-grey-secondary">{impact.not_known.join(" ")}</p>
      ) : null}
      <Fold summary="Details">
        <div className="grid gap-3">
          <div
            role="group"
            aria-label="Which date"
            className="inline-flex w-fit rounded-full border border-grey-border bg-(--op-viz-surface) p-0.5"
          >
            {(["p85", "p50"] as const).map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={measure === value}
                onClick={() => onMeasure(value)}
                className={cn(
                  "h-8 rounded-full px-3 text-[13px] font-bold max-sm:h-10",
                  measure === value
                    ? "bg-ink text-(--op-viz-surface)"
                    : "text-grey-body hover:bg-grey-fill",
                )}
              >
                {measureWords(value)}
              </button>
            ))}
          </div>
          {bars.length > 0 ? <CauseTable bars={bars} /> : null}
        </div>
      </Fold>
    </div>
  );
}

/** The causes as bars from zero: red to the right pushed it later, green to the left pulled it in. */
function Bars({ bars }: { bars: Bar[] }) {
  const [opened, setOpened] = useState<string | null>(null);
  return (
    <ul className="m-0 grid list-none gap-1.5 p-0" aria-label="What moved the date">
      {bars.map((bar) => {
        const items = bar.driver.items;
        const open = opened === bar.id;
        // Half the area each side of zero, less room for the days at the bar's end.
        const width = `max(2px, calc((50% - 72px) * ${bar.size.toFixed(3)}))`;
        return (
          <li key={bar.id} className="grid min-w-0 gap-1">
            <button
              type="button"
              aria-expanded={items.length > 0 ? open : undefined}
              disabled={items.length === 0}
              onClick={() => setOpened(open ? null : bar.id)}
              className="grid min-w-0 grid-cols-[minmax(0,1fr)] items-center gap-x-4 gap-y-1 rounded-lg px-1 py-1.5 text-left enabled:hover:bg-grey-fill sm:grid-cols-[240px_minmax(0,1fr)]"
              aria-label={`${bar.label}: ${bar.later ? "pushed it later" : "pulled it in"}, ${daysWords(bar.value)}. ${bar.note}`}
            >
              <span className="min-w-0 text-[13.5px] font-bold text-ink">
                {bar.label}
                {bar.note ? (
                  <span className="block text-[12px] font-normal text-grey-secondary">
                    {bar.note}
                  </span>
                ) : null}
              </span>
              <span aria-hidden className="relative block h-6">
                <span className="absolute top-0 bottom-0 left-1/2 border-l border-(--op-viz-axis)" />
                <span
                  className={cn(
                    "absolute top-1 h-4 rounded-sm",
                    bar.later ? "left-1/2 bg-(--op-red)" : "right-1/2 bg-(--op-green)",
                  )}
                  style={{ width }}
                />
                <span
                  className={cn(
                    "absolute top-0.5 text-[13px] font-extrabold whitespace-nowrap tabular-nums",
                    bar.later ? "text-(--op-red)" : "text-(--op-green)",
                  )}
                  style={
                    bar.later
                      ? { left: `calc(50% + ${width} + 6px)` }
                      : { right: `calc(50% + ${width} + 6px)` }
                  }
                >
                  {daysWords(bar.value)}
                </span>
              </span>
            </button>
            {open ? (
              <ul className="m-0 ml-1 grid list-none gap-0.5 border-l-2 border-grey-border p-0 pl-3 text-[12.5px] text-grey-body">
                {items.map((item) => (
                  <li key={item.key}>
                    <b className="font-extrabold text-ink">{item.key}</b> {item.title}
                    <span className="text-grey-secondary">
                      {item.day ? ` · ${shortDay(item.day)}` : ""}
                      {item.change_label === true ? " · change label" : ""}
                      {item.change_label === false ? " · no change label" : ""}
                      {item.added_by ? ` · added by ${item.added_by}` : ""}
                    </span>
                  </li>
                ))}
                {bar.driver.more > 0 ? <li>and {bar.driver.more} more</li> : null}
              </ul>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

function CauseTable({ bars }: { bars: Bar[] }) {
  return (
    <TableBox>
      <table className="w-full min-w-[520px] border-collapse">
        <caption className="sr-only">What moved the date</caption>
        <thead>
          <tr>
            {["Cause", "Working days", "Requirements", "Note"].map((head) => (
              <th key={head} scope="col" className={th}>
                {head}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {bars.map((bar) => (
            <tr key={bar.id}>
              <th scope="row" className={`${td} text-left font-bold`}>
                {bar.label}
              </th>
              <td className={`${td} tabular-nums`}>{signed(bar.value)}</td>
              <td className={td}>
                {bar.driver.items.map((item) => item.key).join(", ") || "—"}
                {bar.driver.more > 0 ? ` and ${bar.driver.more} more` : ""}
              </td>
              <td className={td}>{bar.note || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableBox>
  );
}

/** With no move to take apart (no likely date yet), what joined and left the scope. */
function Ledger({ impact }: { impact: DeliveryImpactResponse }) {
  const changes = impact.scope_changes;
  const line = (label: string, keys: string[], count: number) =>
    count > 0
      ? `${label}: ${keys.join(", ")}${count > keys.length ? ` and ${count - keys.length} more` : ""}`
      : null;
  const lines = [
    line(
      "Added",
      changes.added.map((item) => item.key),
      changes.added_count,
    ),
    line(
      "Removed",
      changes.removed.map((item) => item.key),
      changes.removed_count,
    ),
    line(
      "Reopened",
      changes.reopened.map((item) => item.key),
      changes.reopened_count,
    ),
    changes.finished_count > 0 ? `Finished: ${changes.finished_count}` : null,
  ].filter((text): text is string => text !== null);
  if (lines.length === 0) return null;
  return (
    <ul className="m-0 grid list-none gap-1 p-0 text-[13px] text-grey-body">
      {lines.map((text) => (
        <li key={text}>{text}</li>
      ))}
    </ul>
  );
}

/** Short lines: the likely date at both ends, the committed date, the team's date, no date yet. */
function Lanes({ impact, ends }: { impact: DeliveryImpactResponse; ends: string | null }) {
  const team = impact.team_date;
  const lines: [string, string][] = [];
  if (ends) lines.push(["Likely date", ends]);
  if (impact.committed_changes.length > 0) {
    lines.push(["Committed date", impact.committed_changes.map(committedLine).join("; ")]);
  }
  if (team) lines.push(["Team's own date", teamLine(team)]);
  if (impact.evidence && impact.evidence.undated.length > 0) {
    lines.push(["No date yet", impact.evidence.undated.join(", ")]);
  }
  if (lines.length === 0) return null;
  return (
    <dl className="m-0 grid gap-1 text-[13px] text-grey-body">
      {lines.map(([label, text]) => (
        <div key={label} className="flex flex-wrap gap-x-1.5">
          <dt className="font-bold text-ink">{label}:</dt>
          <dd className="m-0 min-w-0">{text}</dd>
        </div>
      ))}
    </dl>
  );
}
