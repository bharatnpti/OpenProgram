import type { DeliveryStage, RequirementsResponse } from "../../api/schema";
import { useViewingDate } from "../../app/viewingDate";
import { PanelState, SectionHeader, TableBox, td, th } from "../../components/PanelState";
import { Card } from "../../components/ui/Card";
import { formatDay, signedChange } from "../../lib/format";
import { STAGE_LABELS, STAGE_ORDER, stageColor } from "../../lib/status";
import { stageStack } from "./charts";
import {
  historyStart,
  inStageSinceWords,
  noRequirementsWords,
  noSnapshotsWords,
  releaseName,
  timelineTitle,
} from "./overallWords";
import { PROJECT_PROGRESS_READERS, REQUIREMENT_DAYS, useRequirements } from "./queries";

/**
 * How many requirements sit in each delivery stage, how that changed, and each
 * one's place, for the whole project or one release.
 */
export function RequirementsSection({
  projectId,
  releaseId = "",
}: {
  projectId: string;
  releaseId?: string;
}) {
  const { locked, query } = useRequirements(projectId, releaseId || undefined);
  const { asOf, label } = useViewingDate();
  const data = query.data;

  return (
    <section>
      <SectionHeader
        title={
          data?.release_name
            ? `Requirements by stage: ${releaseName(data.release_name)}`
            : "Requirements by stage"
        }
        meta={
          data
            ? `${data.total} requirements · ${data.live ? "read live today" : `as of ${formatDay(data.as_of)}`}${
                data.previous_day ? ` · change since ${formatDay(data.previous_day)}` : ""
              }`
            : undefined
        }
      />
      <PanelState
        locked={locked}
        needs={PROJECT_PROGRESS_READERS}
        isLoading={query.isLoading}
        error={query.error}
        onRetry={() => void query.refetch()}
        isEmpty={data ? !data.available || data.total === 0 : false}
        emptyText={noRequirementsWords(asOf ? label : null)}
      >
        {data ? (
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
            <StageCards data={data} />
            <Card padding="p-5">
              <h3 className="text-[15px] font-extrabold">{timelineTitle(data.timeline.length)}</h3>
              <p className="mb-3 text-[12px] text-grey-secondary">
                One bar per day, from the daily snapshot
              </p>
              <StageTimeline data={data} />
            </Card>
            {data.moves.length > 0 ? <Moves data={data} /> : null}
            <RequirementTable data={data} />
            {data.unmapped_statuses.length > 0 ? (
              <p className="text-[12px] text-grey-secondary">
                Not placed in a stage, counted by their broad state:{" "}
                {data.unmapped_statuses.join(", ")}.
              </p>
            ) : null}
          </div>
        ) : null}
      </PanelState>
    </section>
  );
}

function labelFor(data: RequirementsResponse, stage: DeliveryStage): string {
  return data.stages.find((s) => s.stage === stage)?.label ?? STAGE_LABELS[stage];
}

function StageCards({ data }: { data: RequirementsResponse }) {
  return (
    <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
      {STAGE_ORDER.map((stage) => {
        const row = data.stages.find((s) => s.stage === stage);
        const change = row?.change ?? null;
        return (
          <li
            key={stage}
            className="rounded-2xl border-t-4 bg-grey-header p-3"
            style={{ borderTopColor: stageColor(stage) }}
          >
            <p className="text-[11px] font-bold uppercase tracking-wide text-grey-secondary">
              {labelFor(data, stage)}
            </p>
            <p className="mt-1 text-[24px] font-extrabold tabular-nums">
              {row?.count ?? 0}
              {change !== null ? (
                <span
                  className={`ml-1.5 text-[12px] font-bold ${change > 0 ? "text-rag-green" : "text-grey-secondary"}`}
                >
                  {signedChange(change)}
                </span>
              ) : null}
            </p>
            {data.has_points && row ? (
              <p className="text-[11px] text-grey-secondary">{row.points} points</p>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

function StageTimeline({ data }: { data: RequirementsResponse }) {
  const { asOf, label } = useViewingDate();
  const width = 1000;
  const height = 220;
  const { bars, yTicks } = stageStack(data.timeline, STAGE_ORDER, {
    width,
    height,
    left: 30,
    right: 8,
    top: 10,
    bottom: 24,
  });
  if (bars.length === 0) {
    return (
      <p className="text-[13px] text-grey-secondary">
        {asOf ? noSnapshotsWords(label) : "No daily snapshots yet."}
      </p>
    );
  }
  // One day is a single block, not a timeline: say what it holds instead.
  if (bars.length === 1) {
    return (
      <p className="rounded-2xl bg-grey-fill px-4 py-3 text-[13px] text-grey-body">
        Only one daily snapshot so far ({formatDay(bars[0].day)}); the counts are in the cards
        above. The bars draw from the second day.
      </p>
    );
  }
  const first = bars[0];
  const last = bars[bars.length - 1];

  return (
    <figure className="m-0">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="block h-auto w-full max-w-full"
        role="img"
        aria-label={`Requirements in each delivery stage, one bar per day from ${formatDay(first.day)} to ${formatDay(last.day)}.`}
      >
        {yTicks.map((tick) => (
          <g key={tick.value}>
            <line x1={30} x2={width - 8} y1={tick.y} y2={tick.y} stroke="var(--op-grey-border)" />
            <text
              x={25}
              y={tick.y + 4}
              textAnchor="end"
              fontSize="11"
              fill="var(--op-grey-secondary)"
            >
              {tick.value}
            </text>
          </g>
        ))}
        {bars.map((bar) => (
          <g key={bar.day}>
            {bar.segments.map((segment) => (
              <rect
                key={segment.stage}
                x={bar.x}
                y={segment.y}
                width={bar.width}
                height={segment.height}
                fill={stageColor(segment.stage)}
              >
                <title>{`${formatDay(bar.day)} · ${labelFor(data, segment.stage)}: ${segment.count}`}</title>
              </rect>
            ))}
          </g>
        ))}
        <text x={first.x} y={height - 6} fontSize="11" fill="var(--op-grey-secondary)">
          {formatDay(first.day)}
        </text>
        <text
          x={last.x + last.width}
          y={height - 6}
          textAnchor="end"
          fontSize="11"
          fill="var(--op-grey-secondary)"
        >
          {formatDay(last.day)}
        </text>
      </svg>
      <figcaption className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-grey-body">
        {STAGE_ORDER.map((stage) => (
          <span key={stage} className="inline-flex items-center gap-1.5">
            <span
              className="inline-block h-2.5 w-2.5 rounded-sm"
              style={{ background: stageColor(stage) }}
            />
            {labelFor(data, stage)}
          </span>
        ))}
      </figcaption>
    </figure>
  );
}

function Moves({ data }: { data: RequirementsResponse }) {
  return (
    <div>
      <h3 className="mb-2 text-[15px] font-extrabold">
        Moved since {data.previous_day ? formatDay(data.previous_day) : "the last snapshot"}
      </h3>
      <ul className="grid grid-cols-[minmax(0,1fr)] gap-1 text-[14px] text-grey-body">
        {data.moves.map((move) => (
          <li key={move.key}>
            <span className="font-bold text-ink">{move.key}</span> {move.title}:{" "}
            {move.from_stage ? labelFor(data, move.from_stage) : "new in scope"} →{" "}
            {move.to_stage ? labelFor(data, move.to_stage) : "out of scope"}
          </li>
        ))}
      </ul>
    </div>
  );
}

function RequirementTable({ data }: { data: RequirementsResponse }) {
  const start = historyStart(data.timeline, REQUIREMENT_DAYS);
  const rows = [...data.requirements].sort(
    (a, b) =>
      STAGE_ORDER.indexOf(b.stage) - STAGE_ORDER.indexOf(a.stage) || a.key.localeCompare(b.key),
  );
  return (
    <>
      <TableBox>
        <table className="w-full min-w-[520px] sm:min-w-[760px] border-collapse">
          <thead>
            <tr>
              <th className={th}>Requirement</th>
              <th className={th}>Stage</th>
              <th className={th}>In stage since</th>
              <th className={th}>Jira status</th>
              <th className={th}>Assignee</th>
              {data.has_points ? <th className={`${th} text-right`}>Points</th> : null}
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
                  <span
                    className="inline-flex items-center gap-1.5 font-bold"
                    style={{ color: stageColor(req.stage) }}
                  >
                    <span
                      className="inline-block h-2 w-2 rounded-full"
                      style={{ background: stageColor(req.stage) }}
                    />
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
