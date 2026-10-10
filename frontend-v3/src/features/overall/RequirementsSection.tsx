import type { DeliveryStage, RequirementsResponse } from "../../api/schema";
import { useViewingDate } from "../../app/viewingDate";
import { PanelState, SectionHeader, TableBox, td, th } from "../../components/PanelState";
import { Fold } from "../../components/viz/Fold";
import { STAGE_LABELS, STAGE_ORDER, stageColor } from "../../components/viz/stages";
import { formatDay } from "../../lib/format";
import {
  flowDrawable,
  flowFinding,
  flowGeometry,
  flowSpoken,
  shortHistoryNote,
  splitFinding,
} from "./flow";
import { historyStart, inStageSinceWords, noRequirementsWords, releaseName } from "./overallWords";
import { REQUIREMENT_DAYS, useRequirements } from "./queries";
import { Legend, Swatch, VizCard } from "./viz";
import { SVG_TEXT } from "./vizStyles";

/**
 * O3, "Requirements by stage": where work piles up. A cumulative flow of each
 * stage over the daily snapshots, with today's counts at its right edge; under
 * ten working days of history, one bar of today's split with the six counts
 * beside it. It replaces the six stage cards, the 30-day stacked bars and the
 * "Moved since" list (since yesterday is Daily's). Every requirement, with its
 * stage and how long it has been there, folds behind "Show all".
 */
export function RequirementsSection({
  projectId,
  releaseId = "",
}: {
  projectId: string;
  releaseId?: string;
}) {
  const { query } = useRequirements(projectId, releaseId || undefined);
  const { asOf, label } = useViewingDate();
  const data = query.data;

  return (
    <section
      aria-labelledby="o3-h"
      // Drawn as it nears the viewport: a month of bands is the page's heaviest SVG.
      className="[contain-intrinsic-size:auto_520px] [content-visibility:auto]"
    >
      <SectionHeader
        id="o3-h"
        title={
          data?.release_name
            ? `Requirements by stage: ${releaseName(data.release_name)}`
            : "Requirements by stage"
        }
        meta={
          data
            ? `${data.total} requirements · ${data.live ? "read live today" : `as of ${formatDay(data.as_of)}`}`
            : undefined
        }
      />
      <PanelState
        isLoading={query.isLoading}
        error={query.error}
        onRetry={() => void query.refetch()}
        isEmpty={data ? !data.available || data.total === 0 : false}
        emptyText={noRequirementsWords(asOf ? label : null)}
      >
        {data ? (
          <VizCard>
            {flowDrawable(data.timeline) ? <Flow data={data} /> : <Split data={data} />}
            <Fold summary={`Show all ${data.total} requirements`}>
              <RequirementTable data={data} />
            </Fold>
            {data.unmapped_statuses.length > 0 ? (
              <p className="text-[12px] text-grey-secondary">
                Not placed in a stage, counted by their broad state:{" "}
                {data.unmapped_statuses.join(", ")}.
              </p>
            ) : null}
          </VizCard>
        ) : null}
      </PanelState>
    </section>
  );
}

function labelFor(data: RequirementsResponse, stage: DeliveryStage): string {
  return data.stages.find((s) => s.stage === stage)?.label ?? STAGE_LABELS[stage];
}

function countOf(data: RequirementsResponse, stage: DeliveryStage): number {
  return data.stages.find((s) => s.stage === stage)?.count ?? 0;
}

/** Under ten working days: one bar of today's split, nothing coloured that history can't back. */
function Split({ data }: { data: RequirementsResponse }) {
  const label = (stage: DeliveryStage) => labelFor(data, stage);
  const counts = Object.fromEntries(STAGE_ORDER.map((stage) => [stage, countOf(data, stage)]));
  const shown = STAGE_ORDER.filter((stage) => countOf(data, stage) > 0);
  return (
    <div className="grid gap-2.5">
      <p className="text-[15px] font-extrabold">{splitFinding(counts, label)}</p>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-x-6 gap-y-3 xl:grid-cols-[minmax(0,620px)_minmax(0,1fr)] xl:items-start">
        <div>
          <div
            className="flex h-[22px] max-w-[520px] gap-0.5 overflow-hidden rounded-md"
            role="img"
            aria-label={`Today's split of ${data.total} requirements: ${shown.map((stage) => `${label(stage)} ${countOf(data, stage)}`).join(", ")}`}
          >
            {shown.map((stage) => (
              <span
                key={stage}
                className="block h-full"
                style={{ flex: countOf(data, stage), background: stageColor(stage) }}
              />
            ))}
          </div>
          <p className="mt-2 text-[12.5px] text-grey-secondary">
            {shortHistoryNote(data.timeline)}
          </p>
        </div>
        <StageCounts data={data} />
      </div>
    </div>
  );
}

/** The six stages and today's count of each: what the stage cards said. */
function StageCounts({ data }: { data: RequirementsResponse }) {
  return (
    <ul
      className="m-0 grid list-none grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-x-3.5 gap-y-1.5 p-0 text-[13px] text-grey-body"
      aria-label="Requirements per stage today"
    >
      {STAGE_ORDER.map((stage) => (
        <li key={stage} className="flex items-center gap-2">
          <Swatch style={{ background: stageColor(stage) }} />
          {labelFor(data, stage)}
          <b className="ml-auto tabular-nums text-ink">{countOf(data, stage)}</b>
        </li>
      ))}
    </ul>
  );
}

function Flow({ data }: { data: RequirementsResponse }) {
  const label = (stage: DeliveryStage) => labelFor(data, stage);
  const g = flowGeometry(data.timeline, label);
  const { width, height, left, plotRight, top, bottom } = g.size;
  return (
    <div className="grid gap-2.5">
      <p className="text-[15px] font-extrabold">{flowFinding(data.timeline, label).text}</p>
      <figure className="m-0 min-w-0 max-w-[620px]">
        <div
          className="overflow-x-auto rounded-lg"
          tabIndex={0}
          role="region"
          aria-label="Cumulative flow, scrolls sideways"
        >
          <svg
            viewBox={`0 0 ${width} ${height}`}
            className="block h-auto w-full min-w-[480px] overflow-visible"
            role="img"
            aria-label={flowSpoken(data.timeline, label)}
          >
            {g.yTicks.map((tick) => (
              <g key={tick.value}>
                <line
                  x1={left}
                  x2={plotRight}
                  y1={tick.y}
                  y2={tick.y}
                  stroke={tick.value === 0 ? "var(--op-viz-axis)" : "var(--op-viz-grid)"}
                />
                <text x={left - 8} y={tick.y + 4} textAnchor="end" style={SVG_TEXT.muted}>
                  {tick.value}
                </text>
              </g>
            ))}
            {g.xLabels.map((tick) => (
              <text
                key={tick.label}
                x={tick.x}
                y={bottom + 18}
                textAnchor={tick.anchor}
                style={SVG_TEXT.muted}
              >
                {tick.label}
              </text>
            ))}
            <text
              x={plotRight}
              y={bottom + 18}
              textAnchor="end"
              style={{ ...SVG_TEXT.muted, fontWeight: 700 }}
            >
              Today
            </text>
            {g.bands.map((band) => (
              <polygon key={band.stage} points={band.points} fill={stageColor(band.stage)}>
                <title>{band.title}</title>
              </polygon>
            ))}
            {g.edges.map((points) => (
              <polyline
                key={points.slice(-40)}
                points={points}
                fill="none"
                stroke="var(--op-viz-surface)"
                strokeWidth={1.5}
                strokeLinejoin="round"
              />
            ))}
            {g.callout ? (
              <g>
                <line
                  x1={left + 120}
                  y1={top + 25}
                  x2={g.callout.x}
                  y2={g.callout.y - 5}
                  stroke="var(--op-black)"
                  strokeWidth={1.25}
                />
                <rect
                  x={left + 8}
                  y={top + 1}
                  width={g.callout.text.length * 6.6 + 20}
                  height={24}
                  rx={8}
                  fill="var(--op-viz-surface)"
                  stroke="var(--op-grey-border)"
                />
                <text x={left + 18} y={top + 17} style={SVG_TEXT.ink}>
                  {g.callout.text}
                </text>
                <circle
                  cx={g.callout.x}
                  cy={g.callout.y}
                  r={4}
                  fill="var(--op-black)"
                  stroke="var(--op-viz-surface)"
                  strokeWidth={2}
                />
              </g>
            ) : null}
            {g.labels.map((item) => (
              <g key={item.stage}>
                <rect
                  x={plotRight + 12}
                  y={item.y - 9}
                  width={10}
                  height={10}
                  rx={2}
                  fill={stageColor(item.stage)}
                />
                <text x={plotRight + 28} y={item.y} style={SVG_TEXT.ink}>
                  {item.text}
                </text>
                {/* Secondary words, not amber: amber means a verdict or a gate state. */}
                {item.was ? (
                  <text
                    x={plotRight + 28}
                    y={item.y + 14}
                    style={{ ...SVG_TEXT.muted, fontWeight: 700 }}
                  >
                    {item.was}
                  </text>
                ) : null}
              </g>
            ))}
          </svg>
        </div>
        <div className="mt-2.5">
          <Legend>
            {STAGE_ORDER.map((stage) => (
              <span key={stage}>
                <Swatch style={{ background: stageColor(stage) }} />
                {labelFor(data, stage)}
              </span>
            ))}
          </Legend>
        </div>
        <p className="mt-2 text-[12.5px] text-grey-secondary">
          A band that widens means work arrives faster than it leaves. Today&apos;s counts are at
          the right edge.
        </p>
      </figure>
    </div>
  );
}

function RequirementTable({ data }: { data: RequirementsResponse }) {
  const start = historyStart(data.timeline, REQUIREMENT_DAYS);
  const rows = [...data.requirements].sort(
    (a, b) =>
      STAGE_ORDER.indexOf(b.stage) - STAGE_ORDER.indexOf(a.stage) ||
      a.key.localeCompare(b.key, undefined, { numeric: true }),
  );
  return (
    <>
      <TableBox>
        <table className="w-full min-w-[520px] border-collapse sm:min-w-[760px]">
          <caption className="sr-only">All {data.total} requirements, furthest stage first</caption>
          <thead>
            <tr>
              <th scope="col" className={th}>
                Requirement
              </th>
              <th scope="col" className={th}>
                Stage
              </th>
              <th scope="col" className={th}>
                In stage since
              </th>
              <th scope="col" className={th}>
                Jira status
              </th>
              <th scope="col" className={th}>
                Assignee
              </th>
              {data.has_points ? (
                <th scope="col" className={`${th} text-right`}>
                  Points
                </th>
              ) : null}
            </tr>
          </thead>
          <tbody>
            {rows.map((req) => (
              <tr key={req.key}>
                <td className={td}>
                  {/* Capped on a phone, so the stage beside it is in the first screenful. */}
                  <div className="max-w-[14rem] sm:max-w-none">
                    <span className="font-bold">{req.key}</span> {req.title}
                  </div>
                </td>
                <td className={`${td} whitespace-nowrap`}>
                  <span className="inline-flex items-center gap-1.5 font-bold">
                    <Swatch style={{ background: stageColor(req.stage) }} />
                    {labelFor(data, req.stage)}
                  </span>
                </td>
                <td className={`${td} whitespace-nowrap`}>
                  {inStageSinceWords(req.in_stage_since, start, formatDay)}
                </td>
                <td className={td}>
                  {req.status ?? "—"}
                  {!req.mapped ? (
                    <span className="ml-1 text-[11px] text-grey-secondary">unplaced</span>
                  ) : null}
                </td>
                <td className={td}>{req.assignee_name ?? "—"}</td>
                {data.has_points ? (
                  <td className={`${td} text-right tabular-nums`}>{req.story_points ?? "—"}</td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      </TableBox>
      {start && rows.some((req) => req.in_stage_since === start) ? (
        <p className="mt-1.5 text-[11px] text-grey-secondary">
          The daily history starts on {formatDay(start)}, so a requirement that has not moved since
          reads &ldquo;or earlier&rdquo;: it may have been in its stage longer.
        </p>
      ) : null}
    </>
  );
}
