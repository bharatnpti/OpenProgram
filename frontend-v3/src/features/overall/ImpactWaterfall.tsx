import { useState } from "react";

import type { DeliveryImpactResponse, Measure } from "../../api/schema";
import { PanelState, TableBox, td, th } from "../../components/PanelState";
import { STAGE_LABELS } from "../../components/viz/stages";
import { cn } from "../../lib/utils";
import { shortDay, type Row } from "./timeline";
import { VizCard } from "./viz";
import {
  committedLine,
  endsLine,
  lateLine,
  measureWords,
  movedWords,
  percentOf,
  signed,
  teamLine,
  waterfall,
  type Bar,
} from "./waterfall";

/**
 * "Why the date moved", for the row picked on the timeline over the period its
 * chips pick (DESIGN §3.8): the server's answer line, a horizontal waterfall in
 * working days from the 85% (or 50%) date then to now, worst first, each bar
 * opening its requirements; the committed date's moves, the team's date and
 * the late and undated requirements as evidence. A table stands in for the
 * bars. Drawn only for a row the reader has whole; the server decides the rest.
 */
export function ImpactWaterfall({
  row,
  read,
  measure,
  onMeasure,
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
}) {
  const [asTable, setAsTable] = useState(false);
  const impact = read.data;
  return (
    <VizCard aria-label={`Why the ${measureWords(measure)} moved: ${row.name}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h3 className="min-w-0 text-[16px] font-extrabold text-balance">
          Why the {measureWords(measure)} moved · {row.name}
          {impact ? (
            <span className="font-bold text-grey-secondary"> · {impact.period_words}</span>
          ) : null}
        </h3>
        <div className="flex flex-wrap gap-2">
          <div
            role="group"
            aria-label="Which date"
            className="inline-flex rounded-full border border-grey-border bg-(--op-viz-surface) p-0.5"
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
                {value === "p85" ? "85%" : "50%"}
              </button>
            ))}
          </div>
          <button
            type="button"
            aria-pressed={asTable}
            onClick={() => setAsTable((now) => !now)}
            className={cn(
              "h-9 rounded-full border px-3.5 text-[13px] font-bold max-sm:h-10",
              asTable
                ? "border-ink bg-ink text-(--op-viz-surface)"
                : "border-grey-border bg-(--op-viz-surface) text-grey-body hover:bg-grey-fill",
            )}
          >
            Table
          </button>
        </div>
      </div>
      <PanelState isLoading={read.isLoading} error={read.error} onRetry={() => void read.refetch()}>
        {impact ? <Explained impact={impact} measure={measure} asTable={asTable} /> : null}
      </PanelState>
    </VizCard>
  );
}

function Explained({
  impact,
  measure,
  asTable,
}: {
  impact: DeliveryImpactResponse;
  measure: Measure;
  asTable: boolean;
}) {
  const falls = waterfall(impact.drivers, measure, impact.change_label);
  const ends = endsLine(impact, measure);
  return (
    <div className="grid min-w-0 gap-4">
      <p className="text-[14.5px] font-bold text-ink text-pretty">{impact.summary}</p>
      {falls.bars.length > 0 ? (
        asTable ? (
          <DriverTable bars={falls.bars} total={falls.total} ends={ends} />
        ) : (
          <Bars falls={falls} ends={ends} measure={measure} />
        )
      ) : (
        <Ledger impact={impact} />
      )}
      <Lanes impact={impact} />
      {impact.not_known.length > 0 ? (
        <ul className="m-0 grid list-none gap-1 p-0 text-[12.5px] text-grey-secondary">
          {impact.not_known.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/** The waterfall: one bar per driver from where the one before ended, the move last. */
function Bars({
  falls,
  ends,
  measure,
}: {
  falls: ReturnType<typeof waterfall>;
  ends: string | null;
  measure: Measure;
}) {
  const [opened, setOpened] = useState<string | null>(null);
  const zero = percentOf(falls, 0);
  return (
    <div className="grid min-w-0 gap-1">
      <ul className="m-0 grid list-none gap-1 p-0" aria-label="What moved the date, worst first">
        {falls.bars.map((bar) => {
          const from = percentOf(falls, Math.min(bar.from, bar.to));
          const to = percentOf(falls, Math.max(bar.from, bar.to));
          const items = bar.driver.items;
          const open = opened === bar.id;
          return (
            <li key={bar.id} className="grid min-w-0 gap-1">
              <button
                type="button"
                aria-expanded={items.length > 0 ? open : undefined}
                disabled={items.length === 0}
                onClick={() => setOpened(open ? null : bar.id)}
                className="grid min-w-0 grid-cols-[minmax(0,1fr)_56px] items-center gap-x-3 gap-y-1 rounded-lg px-1 py-1 text-left enabled:hover:bg-grey-fill sm:grid-cols-[230px_minmax(0,1fr)_56px]"
                aria-label={`${bar.label}: ${signed(bar.value)} working days. ${bar.note}`}
              >
                <span className="min-w-0 text-[13px] font-bold text-ink">
                  {bar.label}
                  <span className="block text-[11.5px] font-normal text-grey-secondary">
                    {bar.note}
                  </span>
                </span>
                <span
                  aria-hidden
                  className="relative col-span-2 h-4 sm:col-span-1 max-sm:order-last"
                >
                  <span
                    className="absolute -top-1 -bottom-1 border-l border-(--op-viz-axis)"
                    style={{ left: `${zero}%` }}
                  />
                  <span
                    className={cn(
                      "absolute top-0.5 h-3 rounded-sm",
                      bar.later ? "bg-ink" : "border-[1.5px] border-ink bg-(--op-viz-surface)",
                    )}
                    style={{ left: `${from}%`, width: `${Math.max(to - from, 0.8)}%` }}
                  />
                </span>
                <span className="text-right text-[13px] font-extrabold tabular-nums text-ink">
                  {signed(bar.value)}
                </span>
              </button>
              {open ? (
                <ul className="m-0 ml-1 grid list-none gap-0.5 border-l-2 border-grey-border p-0 pl-3 text-[12.5px] text-grey-body">
                  {items.map((item) => (
                    <li key={item.key}>
                      <b className="font-extrabold text-ink">{item.key}</b> {item.title}
                      <span className="text-grey-secondary">
                        {item.type ? ` · ${item.type}` : ""}
                        {item.day ? ` · ${shortDay(item.day)}` : ""}
                        {item.change_label === true ? " · change label" : ""}
                        {item.change_label === false ? " · no change label" : ""}
                        {item.added_by ? ` · added by ${item.added_by}` : ""} ·{" "}
                        {signed(item.working_days)}
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
      <p className="mt-1 flex flex-wrap items-baseline justify-between gap-x-3 border-t border-grey-border pt-2 text-[13.5px]">
        <span className="font-extrabold text-ink">{movedWords(falls.total)}</span>
        {ends ? (
          <span className="text-grey-body">
            {measureWords(measure)}: {ends}
          </span>
        ) : null}
      </p>
    </div>
  );
}

function DriverTable({ bars, total, ends }: { bars: Bar[]; total: number; ends: string | null }) {
  return (
    <TableBox>
      <table className="w-full min-w-[560px] border-collapse">
        <caption className="sr-only">What moved the date, worst first</caption>
        <thead>
          <tr>
            {["What moved it", "Working days", "Requirements", "Note"].map((head) => (
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
              <td className={td}>{bar.note}</td>
            </tr>
          ))}
          <tr>
            <th scope="row" className={`${td} text-left font-extrabold`}>
              Moved
            </th>
            <td className={`${td} font-extrabold tabular-nums`}>{signed(total)}</td>
            <td className={td} colSpan={2}>
              {ends ?? "—"}
            </td>
          </tr>
        </tbody>
      </table>
    </TableBox>
  );
}

/** With no move to take apart (no forecast yet), what joined and left the scope. */
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
      "Left the scope",
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

/** The two other dates, and the evidence that explains pace without a share. */
function Lanes({ impact }: { impact: DeliveryImpactResponse }) {
  const evidence = impact.evidence;
  const team = impact.team_date;
  return (
    <dl className="m-0 grid gap-2.5 text-[13px] text-grey-body">
      {impact.committed_changes.length > 0 ? (
        <div>
          <dt className="font-bold text-ink">Committed date</dt>
          {impact.committed_changes.map((change) => (
            <dd key={`${change.on}:${change.to_date}`} className="m-0">
              {committedLine(change)}
            </dd>
          ))}
        </div>
      ) : null}
      {team ? (
        <div>
          <dt className="font-bold text-ink">Team&apos;s date</dt>
          <dd className="m-0">{teamLine(team)}</dd>
          {team.moves.map((move) => (
            <dd key={`${move.day}:${move.cause}`} className="m-0 text-grey-secondary">
              {shortDay(move.day)}: {move.words}
            </dd>
          ))}
        </div>
      ) : null}
      {evidence && evidence.late.length > 0 ? (
        <div>
          <dt className="font-bold text-ink">Closing late</dt>
          {evidence.late.map((late) => (
            <dd key={late.key} className="m-0">
              {lateLine(late, (stage) => STAGE_LABELS[stage])}
            </dd>
          ))}
          {evidence.more_late > 0 ? (
            <dd className="m-0 text-grey-secondary">and {evidence.more_late} more</dd>
          ) : null}
        </div>
      ) : null}
      {evidence && evidence.undated.length > 0 ? (
        <div>
          <dt className="font-bold text-ink">No date yet</dt>
          <dd className="m-0">{evidence.undated.join(", ")}</dd>
        </div>
      ) : null}
    </dl>
  );
}
