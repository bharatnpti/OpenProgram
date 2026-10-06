import type { RequirementTimelinePointResponse } from "../../api/schema";
import { formatDay } from "../../lib/format";
import { burndownByCount, burndownGeometry, type Marker } from "./charts";

const MARKER_COLORS: Record<string, string> = {
  committed: "var(--op-black)",
  p50: "var(--op-amber)",
  p85: "var(--op-red)",
  team: "var(--op-info)",
};

/**
 * Requirements still to reach production, per day, with the committed date and
 * the forecast dates marked on the same axis.
 */
export function Burndown({
  timeline,
  markers,
}: {
  timeline: RequirementTimelinePointResponse[];
  markers: Marker[];
}) {
  const points = burndownByCount(timeline);
  const g = burndownGeometry(points, markers, (iso) => formatDay(iso).replace(/^\w+ /, ""));

  // One snapshot is a dot, not a trend: say how much history there is instead.
  if (!g.last || points.length < 2) {
    return (
      <p className="rounded-2xl bg-grey-fill px-4 py-3 text-[13px] text-grey-body">
        {points.length === 0
          ? "No daily snapshots yet. The burn-down starts once the first one is kept."
          : `Only one daily snapshot so far (${formatDay(points[0].day)}): ${points[0].remaining} requirements not yet in production. The line draws from the second day.`}
      </p>
    );
  }

  return (
    <figure className="m-0">
      <svg
        viewBox={`0 0 ${g.width} ${g.height}`}
        className="block h-auto w-full max-w-full"
        role="img"
        aria-label={`Requirements not yet in production: ${points[0].remaining} on the first day, ${g.last.remaining} today.`}
      >
        {g.yTicks.map((tick) => (
          <g key={tick.value}>
            <line
              x1={36}
              x2={g.width - 16}
              y1={tick.y}
              y2={tick.y}
              stroke="var(--op-grey-border)"
            />
            <text
              x={30}
              y={tick.y + 4}
              textAnchor="end"
              fontSize="11"
              fill="var(--op-grey-secondary)"
            >
              {tick.value}
            </text>
          </g>
        ))}
        <path d={g.area} fill="var(--op-magenta-tint)" opacity={0.6} />
        <path
          d={g.path}
          fill="none"
          stroke="var(--op-magenta)"
          strokeWidth={2.5}
          strokeLinejoin="round"
        />
        <circle cx={g.last.x} cy={g.last.y} r={4} fill="var(--op-magenta)" />
        {g.markers.map((m) => (
          <line
            key={m.key}
            x1={m.x}
            x2={m.x}
            y1={14}
            y2={g.height - 30}
            stroke={MARKER_COLORS[m.key] ?? "var(--op-grey-secondary)"}
            strokeWidth={m.key === "committed" ? 1.5 : 1.25}
            strokeDasharray={m.key === "committed" ? undefined : "4 3"}
          />
        ))}
        {g.xLabels.map((label) => (
          <text
            key={`${label.label}-${label.anchor}`}
            x={label.x}
            y={g.height - 10}
            textAnchor={label.anchor}
            fontSize="11"
            fill="var(--op-grey-secondary)"
          >
            {label.label}
          </text>
        ))}
      </svg>
      <figcaption className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-grey-body">
        <Legend color="var(--op-magenta)" label="Not yet in production" />
        {g.markers.map((m) => (
          <Legend
            key={m.key}
            color={MARKER_COLORS[m.key] ?? "var(--op-grey-secondary)"}
            label={`${m.label} · ${formatDay(m.day)}`}
          />
        ))}
      </figcaption>
    </figure>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: color }} />
      {label}
    </span>
  );
}
