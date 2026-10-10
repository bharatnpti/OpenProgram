import type { ScopeDeliveryResponse } from "../../api/schema";
import { PanelState, SectionHeader } from "../../components/PanelState";
import { counted, forecastTone } from "../../components/ui/dateStripWords";
import { formatDay } from "../../lib/format";
import { readState } from "../../lib/readState";
import type { BadgeTone } from "../../lib/status";
import { useDelivery, useForecastHistory } from "./queries";
import {
  changeKeys,
  slipDrawable,
  slipFinding,
  slipGeometry,
  slipRecord,
  type Phrase,
} from "./slip";
import { Legend, LineKey, Swatch, VizCard } from "./viz";
import { HATCH, SVG_TEXT } from "./vizStyles";

/**
 * O1, "How the date moved": the committed date as a step line, numbered where
 * it was set and moved, over the history forecast's 50% to 85% range as it
 * stood each day. It replaces the "How the date moved" table; its keys say each
 * change in words. Until the date moves or the forecast has history it is one
 * line: the date and why.
 */
export function SlipSection({ projectId, releaseId }: { projectId: string; releaseId: string }) {
  const delivery = useDelivery(projectId);
  const history = useForecastHistory(projectId, releaseId || undefined);
  const data = delivery.query.data;
  const scope = releaseId
    ? data?.releases.find((item) => item.scope_id === releaseId)
    : data?.project;

  return (
    <section aria-labelledby="o1-h">
      <SectionHeader
        id="o1-h"
        title="How the date moved"
        meta="The committed date, and the forecast as it stood each day"
      />
      <PanelState
        {...readState(history.query, delivery.query)}
        onRetry={() => void history.query.refetch()}
      >
        {scope && history.query.data ? (
          <SlipCard scope={scope} days={history.query.data.days} />
        ) : null}
      </PanelState>
    </section>
  );
}

/**
 * The forecast range's colours: the strip's Forecast cell's (forecastTone, the
 * server's verdict rule against the committed date), so the band never says
 * "at risk" beside a red cell. Grey with no committed date to meet.
 */
const BAND: Record<BadgeTone, { fill: string; stroke: string }> = {
  success: { fill: "var(--op-green-bg)", stroke: "var(--op-green)" },
  warning: { fill: "var(--op-amber-bg)", stroke: "var(--op-amber)" },
  danger: { fill: "var(--op-red-bg)", stroke: "var(--op-red)" },
  info: { fill: "var(--op-unknown-bg)", stroke: "var(--op-unknown)" },
  neutral: { fill: "var(--op-unknown-bg)", stroke: "var(--op-unknown)" },
};

function SlipCard({
  scope,
  days,
}: {
  scope: ScopeDeliveryResponse;
  days: { day: string; p50: string | null; p85: string | null }[];
}) {
  // The read's own last day: the day it was asked for, by the server's clock.
  const today = days[days.length - 1]?.day ?? "";
  const changes = scope.commitment.changes;
  if (!slipDrawable(changes, days)) {
    const record = slipRecord(changes, scope.jira_release_date, scope.history.sample_days);
    return (
      <VizCard>
        <div className="grid gap-1.5 rounded-2xl bg-grey-fill px-3.5 py-3 text-[13.5px] text-grey-body">
          <PhraseText phrase={record.line} />
          <span className="text-[12.5px] text-grey-secondary">{record.note}</span>
        </div>
      </VizCard>
    );
  }
  const g = slipGeometry(changes, days, today);
  const keys = changeKeys(changes);
  const band =
    BAND[
      counted(scope) ? forecastTone(scope.target, scope.history.p50, scope.history.p85) : "neutral"
    ];
  const { width, height, left, plotRight, top, bottom } = g.size;

  return (
    <VizCard>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-x-6 gap-y-4 xl:grid-cols-[minmax(0,620px)_minmax(0,1fr)] xl:items-start">
        <figure className="m-0 min-w-0">
          <div
            className="overflow-x-auto rounded-lg"
            tabIndex={0}
            role="region"
            aria-label="Slip chart, scrolls sideways"
          >
            <svg
              viewBox={`0 0 ${width} ${height}`}
              className="block h-auto w-full min-w-[480px] overflow-visible"
              role="img"
              aria-label={slipFinding(changes, days, g.start)}
            >
              <defs>
                <pattern
                  id="o1-hatch"
                  width="6"
                  height="6"
                  patternUnits="userSpaceOnUse"
                  patternTransform="rotate(45)"
                >
                  <rect width="6" height="6" fill="var(--op-red-bg)" />
                  <line x1="0" y1="0" x2="0" y2="6" stroke="var(--op-red)" strokeWidth="1.5" />
                </pattern>
              </defs>
              {g.yTicks.map((tick) => (
                <g key={tick.label}>
                  <line
                    x1={left}
                    x2={plotRight}
                    y1={tick.y}
                    y2={tick.y}
                    stroke="var(--op-viz-grid)"
                  />
                  <text x={left - 8} y={tick.y + 4} textAnchor="end" style={SVG_TEXT.muted}>
                    {tick.label}
                  </text>
                </g>
              ))}
              <line x1={left} x2={plotRight} y1={bottom} y2={bottom} stroke="var(--op-viz-axis)" />
              {g.xLabels.map((label) => (
                <text
                  key={label.label}
                  x={label.x}
                  y={bottom + 18}
                  textAnchor={label.anchor}
                  style={SVG_TEXT.muted}
                >
                  {label.label}
                </text>
              ))}
              {g.bands.map((points) => (
                <polygon
                  key={points.slice(0, 24)}
                  points={points}
                  fill={band.fill}
                  stroke={band.stroke}
                  strokeOpacity={0.6}
                >
                  <title>Forecast range, 50% to 85% likely, as it stood each day</title>
                </polygon>
              ))}
              {g.bandStart ? (
                <text
                  x={Math.min(g.bandStart.x + 4, plotRight - 150)}
                  y={Math.max(g.bandStart.y - 6, top - 2)}
                  style={{ ...SVG_TEXT.amber, fill: band.stroke }}
                >
                  Forecast from {formatDay(g.bandStart.day)}
                </text>
              ) : null}
              {g.noDate.map((span) => (
                <g key={span.x}>
                  <rect
                    x={span.x}
                    y={bottom - 10}
                    width={Math.max(span.width, 2)}
                    height={10}
                    fill="url(#o1-hatch)"
                  >
                    <title>{span.title}</title>
                  </rect>
                  {span.width > 40 && !nearMark(span, g.marks, bottom - 16) ? (
                    <text x={span.x + 2} y={bottom - 16} style={SVG_TEXT.red}>
                      No date
                    </text>
                  ) : null}
                </g>
              ))}
              <line
                x1={g.todayX}
                x2={g.todayX}
                y1={top - 6}
                y2={bottom}
                stroke="var(--op-viz-axis)"
              />
              <text x={g.todayX} y={top - 12} textAnchor="middle" style={SVG_TEXT.ink}>
                Today
              </text>
              {g.committed.map((d) => (
                <path
                  key={d}
                  d={d}
                  fill="none"
                  stroke="var(--op-black)"
                  strokeWidth={2.5}
                  strokeLinejoin="round"
                />
              ))}
              {g.marks.map((mark) => (
                <g key={mark.n}>
                  <circle
                    cx={mark.x}
                    cy={mark.y}
                    r={4.5}
                    fill="var(--op-black)"
                    stroke="var(--op-viz-surface)"
                    strokeWidth={2}
                  >
                    <title>{mark.title}</title>
                  </circle>
                  <circle cx={mark.badgeX} cy={mark.badgeY} r={8} fill="var(--op-black)" />
                  <text
                    x={mark.badgeX}
                    y={mark.badgeY + 3.6}
                    textAnchor="middle"
                    style={{ ...SVG_TEXT.ink, fill: "var(--op-viz-surface)", fontSize: 11 }}
                  >
                    {mark.n}
                  </text>
                </g>
              ))}
              {g.right.map((label) => (
                <text key={label.text} x={plotRight + 8} y={label.y} style={SVG_TEXT.ink}>
                  {label.text}
                </text>
              ))}
            </svg>
          </div>
        </figure>
        <div className="grid content-start gap-3">
          <ol className="m-0 grid list-none gap-2 p-0 text-[13px] text-grey-body">
            {keys.map((phrase, i) => (
              <li key={changes[i].changed_at} className="flex items-start gap-2">
                <span
                  aria-hidden
                  className="mt-px inline-flex h-[18px] w-[18px] flex-none items-center justify-center rounded-full bg-ink text-[11px] font-bold text-(--op-viz-surface)"
                >
                  {i + 1}
                </span>
                <span className="min-w-0">
                  <span className="sr-only">{i + 1}. </span>
                  <PhraseText phrase={phrase} />
                </span>
              </li>
            ))}
          </ol>
          <Legend>
            <span>
              <LineKey />
              Committed date
            </span>
            {g.bands.length > 0 ? (
              <span>
                <Swatch
                  style={{ background: band.fill, boxShadow: `inset 0 0 0 1.5px ${band.stroke}` }}
                />
                Forecast, 50% to 85% likely
              </span>
            ) : null}
            {g.noDate.length > 0 ? (
              <span>
                <Swatch style={HATCH} />
                No committed date
              </span>
            ) : null}
          </Legend>
        </div>
      </div>
    </VizCard>
  );
}

/** A sentence with its bold run. */
export function PhraseText({ phrase: [before, bold, after] }: { phrase: Phrase }) {
  return (
    <span>
      {before}
      {bold ? <b className="font-extrabold text-ink">{bold}</b> : null}
      {after}
    </span>
  );
}

/**
 * Whether a numbered marker sits where the "No date" word would go; the word
 * then gives way (the legend and the hatch's title still say it).
 */
function nearMark(
  span: { x: number; width: number },
  marks: { badgeX: number; badgeY: number }[],
  y: number,
): boolean {
  return marks.some(
    (mark) =>
      Math.abs(mark.badgeY - y) < 14 && mark.badgeX > span.x - 10 && mark.badgeX < span.x + 60,
  );
}
