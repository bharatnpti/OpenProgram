import { useState } from "react";

import type { RequirementTimelinePointResponse } from "../../api/schema";
import { STAGE_COLORS, STAGE_LABELS, STAGES, countTicks, maxTotal, stackColumns } from "./stages";

const WIDTH = 640;
const HEIGHT = 180;
const LEFT = 32;
const BOTTOM = 22;
const TOP = 8;

/**
 * Requirements by stage, one stacked column per day, production at the base.
 *
 * Every value is also in the table view, so the tooltip only adds to what the
 * table gives. Each day's column is its own hover and focus target.
 */
export function StageTimeline({ points }: { points: RequirementTimelinePointResponse[] }) {
  const [hovered, setHovered] = useState<number | null>(null);
  const [asTable, setAsTable] = useState(false);
  const plotWidth = WIDTH - LEFT;
  const plotHeight = HEIGHT - BOTTOM - TOP;
  const ticks = countTicks(maxTotal(points));
  const scaleMax = ticks[ticks.length - 1] || 1;
  const { columns } = stackColumns(points, { width: plotWidth, height: plotHeight, scaleMax });
  const slot = points.length > 0 ? plotWidth / points.length : plotWidth;
  const hoveredPoint = hovered !== null ? points[hovered] : null;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-grey-secondary">
          {STAGES.map((stage) => (
            <li key={stage} className="flex items-center gap-1.5">
              <span
                aria-hidden
                className="inline-block h-2.5 w-2.5 rounded-[3px]"
                style={{ backgroundColor: STAGE_COLORS[stage] }}
              />
              {STAGE_LABELS[stage]}
            </li>
          ))}
        </ul>
        <button
          type="button"
          className="text-[12px] font-bold text-ink underline-offset-2 hover:underline"
          onClick={() => setAsTable(!asTable)}
        >
          {asTable ? "Show as chart" : "Show as table"}
        </button>
      </div>

      {asTable ? (
        <div className="overflow-x-auto">
          <table className="w-full text-[13px] tabular-nums">
            <thead>
              <tr className="border-b border-grey-border text-left text-grey-secondary">
                <th className="py-1.5 pr-3 font-bold">Day</th>
                {STAGES.map((stage) => (
                  <th key={stage} className="px-2 py-1.5 text-right font-bold">
                    {STAGE_LABELS[stage]}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[...points].reverse().map((point) => (
                <tr key={point.day} className="border-b border-grey-border last:border-0">
                  <td className="py-1.5 pr-3">{dayLabel(point.day)}</td>
                  {STAGES.map((stage) => (
                    <td key={stage} className="px-2 py-1.5 text-right">
                      {point.counts[stage] ?? 0}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="relative">
          <svg
            role="img"
            aria-label="Requirements in each stage per day"
            viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
            className="h-auto w-full"
            onPointerLeave={() => setHovered(null)}
          >
            {ticks.map((tick) => {
              const y = TOP + plotHeight - (tick / scaleMax) * plotHeight;
              return (
                <g key={tick}>
                  <line
                    x1={LEFT}
                    x2={WIDTH}
                    y1={y}
                    y2={y}
                    stroke="var(--op-grey-hover)"
                    strokeWidth={1}
                  />
                  <text
                    x={LEFT - 6}
                    y={y + 4}
                    textAnchor="end"
                    className="fill-[var(--op-grey-secondary)] text-[11px] tabular-nums"
                  >
                    {tick}
                  </text>
                </g>
              );
            })}
            {columns.map((column, index) => (
              <g
                key={column.day}
                transform={`translate(${LEFT}, ${TOP})`}
                opacity={hovered === null || hovered === index ? 1 : 0.55}
              >
                {column.segments.map((segment, position) => {
                  const top = position === column.segments.length - 1;
                  return top ? (
                    <path
                      key={segment.stage}
                      d={roundedTop(column.x, segment.y, column.width, segment.height, 4)}
                      fill={STAGE_COLORS[segment.stage]}
                    />
                  ) : (
                    <rect
                      key={segment.stage}
                      x={column.x}
                      y={segment.y}
                      width={column.width}
                      height={segment.height}
                      fill={STAGE_COLORS[segment.stage]}
                    />
                  );
                })}
              </g>
            ))}
            {columns.map((column, index) => (
              <rect
                key={`hit-${column.day}`}
                x={LEFT + index * slot}
                y={TOP}
                width={slot}
                height={plotHeight}
                fill="transparent"
                tabIndex={0}
                aria-label={`${dayLabel(column.day)}: ${column.total} requirements`}
                onPointerEnter={() => setHovered(index)}
                onFocus={() => setHovered(index)}
                onBlur={() => setHovered(null)}
              />
            ))}
            {axisDays(points).map(({ index, day }) => (
              <text
                key={day}
                x={LEFT + index * slot + slot / 2}
                y={HEIGHT - 6}
                textAnchor="middle"
                className="fill-[var(--op-grey-secondary)] text-[11px]"
              >
                {dayLabel(day)}
              </text>
            ))}
          </svg>
          {hoveredPoint && hovered !== null ? (
            <div
              role="status"
              className="pointer-events-none absolute top-0 z-10 w-[190px] rounded-2xl border border-grey-border bg-white p-3 text-[12px] shadow-op-menu"
              style={{
                left: `${Math.min(78, ((LEFT + (hovered + 0.5) * slot) / WIDTH) * 100)}%`,
              }}
            >
              <div className="mb-1.5 font-bold">{dayLabel(hoveredPoint.day)}</div>
              {[...STAGES].reverse().map((stage) => (
                <div key={stage} className="flex items-center justify-between gap-3">
                  <span className="flex items-center gap-1.5 text-grey-secondary">
                    <span
                      aria-hidden
                      className="inline-block h-[2px] w-3"
                      style={{ backgroundColor: STAGE_COLORS[stage] }}
                    />
                    {STAGE_LABELS[stage]}
                  </span>
                  <strong className="tabular-nums text-ink">
                    {hoveredPoint.counts[stage] ?? 0}
                  </strong>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}

/** A rect whose top corners are rounded: the data end of a column. */
function roundedTop(x: number, y: number, width: number, height: number, radius: number): string {
  const r = Math.min(radius, width / 2, height);
  return [
    `M${x},${y + height}`,
    `V${y + r}`,
    `Q${x},${y} ${x + r},${y}`,
    `H${x + width - r}`,
    `Q${x + width},${y} ${x + width},${y + r}`,
    `V${y + height}`,
    "Z",
  ].join(" ");
}

/** The first, middle and last days, so the axis names its span without crowding. */
function axisDays(points: RequirementTimelinePointResponse[]): { index: number; day: string }[] {
  if (points.length === 0) return [];
  const picks = new Set([0, Math.floor((points.length - 1) / 2), points.length - 1]);
  return [...picks].map((index) => ({ index, day: points[index].day }));
}

function dayLabel(iso: string): string {
  return new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
  });
}
