import { CircleAlert } from "lucide-react";

import { type DateBarChart, type DateFacts, dateBar } from "./dailyViz";
import { DayChip } from "./vizBits";
import { TONE_STROKE, TONE_TEXT } from "./vizColours";

/**
 * D1, the date bar, under In short: will we make the date, and how sure is the
 * forecast? It replaces the report's delivery line and the compact strip that sat
 * above the report. The committed date is a line, the forecast a band from its
 * 50% to its 85% date (or a tick at 85%), the team's latest date a diamond. With
 * too little history the bar is grey and says "No forecast yet": nothing is
 * coloured that history cannot back.
 */
export function DateBar({ facts, today }: { facts: DateFacts; today: string }) {
  const wide = dateBar(facts, today, "wide");
  const narrow = dateBar(facts, today, "narrow");
  // Red only where there is work to deliver: the verdict is "no date" only then.
  const noDate = facts.verdict === "no_date";

  return (
    <figure className="dv-viz">
      <div className="mb-2 flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        <span className="text-[15px] font-extrabold">
          {wide.date}: <span style={{ color: TONE_TEXT[wide.tone] }}>{wide.verdict}</span>
        </span>
        {noDate ? (
          <span className="self-center">
            <DayChip tone="red">
              <CircleAlert aria-hidden className="h-3.5 w-3.5" />
              No committed date
            </DayChip>
          </span>
        ) : null}
        {wide.sub ? <span className="dv-sub">{wide.sub}</span> : null}
      </div>
      {wide.chart && narrow.chart ? (
        <>
          <div className="dv-wide">
            <BarChart chart={wide.chart} className="dv-d1" small={false} />
          </div>
          <div className="dv-narrow">
            <BarChart chart={narrow.chart} small />
          </div>
          {wide.chart.teamNote ? (
            <p className="dv-note mt-1 dv-wide">{wide.chart.teamNote}</p>
          ) : null}
          {narrow.chart.teamNote ? (
            <p className="dv-note mt-1 dv-narrow">{narrow.chart.teamNote}</p>
          ) : null}
        </>
      ) : null}
      {wide.caption ? <p className="dv-note mt-1">{wide.caption}</p> : null}
      {wide.chart ? (
        <div className="dv-legend">
          {wide.legend.committed ? (
            <span>
              <i className="dv-line" />
              {facts.target_source === "jira_release" ? "Jira's release date" : "Committed date"}
            </span>
          ) : null}
          {wide.legend.team ? (
            <span>
              <i className="dv-sw dv-sw-dia" />
              Team's latest date
            </span>
          ) : null}
          {wide.legend.forecast ? (
            <span>
              <i
                className="dv-sw"
                style={{
                  background: forecastFill(wide.tone),
                  boxShadow: `inset 0 0 0 1.5px ${TONE_STROKE[wide.tone]}`,
                }}
              />
              {wide.legend.forecast === "range"
                ? "Forecast, 50% to 85% likely"
                : "Forecast, 85% likely"}
            </span>
          ) : null}
        </div>
      ) : null}
    </figure>
  );
}

const forecastFill = (tone: keyof typeof TONE_TEXT) =>
  tone === "green"
    ? "var(--op-day-green-bg)"
    : tone === "amber"
      ? "var(--op-day-amber-bg)"
      : tone === "red"
        ? "var(--op-day-red-bg)"
        : "var(--op-day-neutral-bg)";

function BarChart({
  chart,
  className,
  small,
}: {
  chart: DateBarChart;
  className?: string;
  small: boolean;
}) {
  const { bar, forecast, team } = chart;
  // The diamond is centred on its day, kept inside the bar.
  const teamX = team ? Math.min(Math.max(team.x, bar.x + 7), bar.x + bar.width - 7) : 0;
  const label = (text: DateBarChart["today"], cls: string) => (
    <text x={text.x} y={text.y} className={cls} textAnchor={text.anchor}>
      {text.text}
    </text>
  );
  const bottom = small ? "dv-tx dv-s" : "dv-tx";
  return (
    <svg
      className={`dv-chart ${className ?? ""}`}
      viewBox={`0 0 ${chart.width} ${chart.height}`}
      role="img"
      aria-label={chart.label}
    >
      {label(chart.today, "dv-tx dv-tx-ink dv-b")}
      {chart.committed ? label(chart.committed.label, "dv-tx dv-tx-ink dv-b") : null}
      {chart.ticks.map((tick) => (
        <text
          key={tick.label}
          x={tick.x}
          y={bar.y - 7}
          className="dv-tx dv-tx-m dv-num"
          textAnchor="middle"
        >
          {tick.label}
        </text>
      ))}
      <rect
        x={bar.x}
        y={bar.y}
        width={bar.width}
        height={bar.height}
        rx={6}
        fill="var(--op-day-surface-2)"
      />
      {chart.ticks.map((tick) => (
        <line
          key={`${tick.label}-line`}
          x1={tick.x}
          x2={tick.x}
          y1={bar.y}
          y2={bar.y + bar.height}
          stroke="var(--op-day-surface)"
          strokeWidth={2}
        />
      ))}
      {chart.noForecast ? label(chart.noForecast, "dv-tx dv-s dv-b") : null}
      {forecast ? (
        <g>
          {forecast.from !== null ? (
            <rect
              x={forecast.from}
              y={bar.y}
              width={Math.max(forecast.to - forecast.from, 2)}
              height={bar.height}
              fill={forecastFill(forecast.tone)}
            >
              <title>Forecast range, 50% to 85% likely</title>
            </rect>
          ) : null}
          {[forecast.from, forecast.to]
            .filter((at): at is number => at !== null)
            .map((at, index) => (
              <line
                key={index}
                x1={at}
                x2={at}
                y1={bar.y - 4}
                y2={bar.y + bar.height + 4}
                stroke={TONE_STROKE[forecast.tone]}
                strokeWidth={2}
              />
            ))}
          {forecast.labels.map((text) => (
            <text
              key={text.text}
              x={text.x}
              y={text.y}
              className={`${bottom} dv-tx-ink dv-b dv-num`}
              textAnchor={text.anchor}
            >
              {text.text}
            </text>
          ))}
        </g>
      ) : null}
      {chart.committed ? (
        <line
          x1={chart.committed.x}
          x2={chart.committed.x}
          y1={chart.committed.top}
          y2={chart.committed.bottom}
          stroke="var(--op-day-ink)"
          strokeWidth={2}
        />
      ) : null}
      {team ? (
        <path
          d={`M${teamX} ${team.y - 7} L${teamX + 7} ${team.y} L${teamX} ${team.y + 7} L${teamX - 7} ${team.y} Z`}
          fill="var(--op-day-info)"
          stroke="var(--op-day-surface)"
          strokeWidth={2}
        >
          <title>{team.title}</title>
        </path>
      ) : null}
      {chart.notes.map((note) => (
        <text key={note.text} x={note.x} y={note.y} className={bottom} textAnchor={note.anchor}>
          {note.text}
        </text>
      ))}
    </svg>
  );
}
