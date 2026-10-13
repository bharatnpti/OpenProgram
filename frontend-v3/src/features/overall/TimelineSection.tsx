import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { apiClient } from "../../api/client";
import type { Measure, PeriodKind } from "../../api/schema";
import { useRole } from "../../app/role";
import { PanelState, SectionHeader } from "../../components/PanelState";
import { Fold } from "../../components/viz/Fold";
import { readState } from "../../lib/readState";
import { cn } from "../../lib/utils";
import { DeliveryDateDialog } from "../reports/DeliveryDateDialog";
import { Locked } from "../reports/ReportDialog";
import { useReportAccess } from "../reports/useReportAccess";
import { PlanningCompleteDialog } from "./BaselineSection";
import { ImpactWaterfall } from "./ImpactWaterfall";
import { useBaselines, useDelivery, useImpact, useTimeline } from "./queries";
import { BandOverTime } from "./SlipChart";
import { TimelineChart } from "./TimelineChart";
import { shortDay, type Row } from "./timeline";
import { VizCard } from "./viz";
import { PERIODS, shadedPeriod } from "./waterfall";

/**
 * Overall's Timeline (DESIGN §5): one row each for the project (or the release
 * picked above), its releases and its pods, from planning to the committed date,
 * with progress, the forecast band, the team's date, the committed date's
 * earlier values and the impact markers; then why the picked row's date moved.
 * It replaces "Dates by pod" and "How the date moved": the committed date's
 * history is each row's ticks, the band's movement is the waterfall, and the
 * daily band is the project row's "Band over time", one level down. The server
 * words every row for the reader: a scrum master gets the pods only, their own
 * whole and the others' dates. `?row=` picks the row the waterfall explains.
 */
export function TimelineSection({
  projectId,
  releaseId,
}: {
  projectId: string;
  releaseId: string;
}) {
  const timeline = useTimeline(projectId, releaseId || undefined);
  const [search, setSearch] = useSearchParams();
  const access = useReportAccess();
  const [period, setPeriod] = useState<PeriodKind>("since_baseline");
  const [measure, setMeasure] = useState<Measure>("p85");
  const [editing, setEditing] = useState<Row | null>(null);
  const [planning, setPlanning] = useState(false);
  const section = useRef<HTMLElement>(null);
  const data = timeline.query.data;
  const rows = data?.rows ?? [];
  const asked = search.get("row");
  const selected =
    rows.find((row) => row.row === asked && row.access === "panel") ??
    rows.find((row) => row.access === "panel") ??
    null;
  // Before planning is marked complete, "since planning" reads the last 30 days.
  const effective: PeriodKind =
    period === "since_baseline" && selected && !selected.baseline ? "last_30_days" : period;
  const impact = useImpact(projectId, selected, effective, measure, selected !== null);
  const head = rows.find((row) => row.scope_kind !== "pod");
  const baselines = useBaselines(projectId, releaseId || undefined);
  const datesOff = access.pastDay("setProjectDates") ?? access.pastDay("setPodDates");

  // A link to #timeline (Delivery's "Why it moved", Ora's link) lands here once it is drawn.
  const landed = useRef(false);
  useEffect(() => {
    if (landed.current || !data || window.location.hash !== "#timeline") return;
    landed.current = true;
    section.current?.scrollIntoView({ block: "start" });
  }, [data]);

  if (data && rows.length === 0) return null;
  const pick = (row: Row) =>
    setSearch(
      (current) => {
        const next = new URLSearchParams(current);
        next.set("row", row.row);
        return next;
      },
      { replace: true },
    );

  return (
    <section id="timeline" ref={section} aria-labelledby="tl-h" className="scroll-mt-4">
      <SectionHeader
        id="tl-h"
        title="Timeline"
        meta={timelineMeta(head, data?.change_label ?? "CR")}
        actions={
          selected ? (
            <Periods
              value={effective}
              planned={Boolean(selected.baseline)}
              onChange={setPeriod}
              onPlan={
                !selected.baseline && access.setProjectDates && baselines.query.data
                  ? () => setPlanning(true)
                  : undefined
              }
            />
          ) : undefined
        }
      />
      <PanelState {...readState(timeline.query)} onRetry={() => void timeline.query.refetch()}>
        {data ? (
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
            <VizCard>
              <TimelineChart
                data={data}
                selected={selected?.row}
                onSelect={pick}
                shade={shadedPeriod(impact.query.data)}
                actions={(row) =>
                  row.can_set_date && !access.readOnly ? (
                    <button
                      type="button"
                      onClick={() => setEditing(row)}
                      aria-label={`${row.committed.date ? "Change" : "Set"} the date, ${row.name}`}
                      className="h-7 rounded-full border border-ink px-2.5 text-[11.5px] font-bold text-ink hover:bg-grey-fill max-sm:h-8"
                    >
                      {row.committed.date ? "Change date" : "Set date"}
                    </button>
                  ) : null
                }
              />
              {datesOff && rows.some((row) => row.can_set_date) ? (
                <Locked>{datesOff}</Locked>
              ) : null}
            </VizCard>
            {head && head.access === "panel" ? (
              <Fold summary={`Band over time: ${head.name}`}>
                <BandOverTime projectId={projectId} releaseId={releaseId} />
              </Fold>
            ) : null}
            {selected ? (
              <ImpactWaterfall
                row={selected}
                read={impact.query}
                measure={measure}
                onMeasure={setMeasure}
              />
            ) : null}
          </div>
        ) : null}
      </PanelState>
      {editing ? (
        <RowDateDialog projectId={projectId} row={editing} onClose={() => setEditing(null)} />
      ) : null}
      {planning && baselines.query.data ? (
        <PlanningCompleteDialog
          projectId={projectId}
          read={baselines.query.data}
          onClose={() => setPlanning(false)}
        />
      ) : null}
    </section>
  );
}

/** "Checkout Revamp · baseline 1 Oct · 18 → 22 requirements". */
function timelineMeta(head: Row | undefined, changeLabel: string): string {
  if (!head) {
    return "Each pod from planning to its committed date, with what moved it";
  }
  const parts = [head.name];
  parts.push(
    head.baseline ? `baseline ${shortDay(head.baseline.day)}` : "no planning baseline yet",
  );
  if (head.scope) parts.push(`${head.scope.at_baseline} → ${head.scope.now} requirements`);
  parts.push(`change label ${changeLabel}`);
  return parts.join(" · ");
}

/**
 * The waterfall's period chips, which also shade the period on the axis. Before
 * planning is marked complete the first is "Mark planning complete", for whoever
 * may mark it; for anyone else it is not drawn.
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

/**
 * Set or change one row's committed date, with the dialog every date uses. It
 * needs the scope's delivery read (who set each earlier date): the project's
 * for a project reader, else the pod's own.
 */
export function RowDateDialog({
  projectId,
  row,
  onClose,
}: {
  projectId: string;
  row: Row;
  onClose: () => void;
}) {
  const { canReadProjectProgress } = useRole();
  const viaPod = row.scope_kind === "pod" && !canReadProjectProgress;
  const project = useDelivery(projectId, !viaPod);
  const pod = useQuery({
    queryKey: ["pod-delivery", row.scope_id],
    queryFn: () => apiClient.podDelivery(row.scope_id),
    enabled: viaPod,
  });
  const scope = viaPod
    ? pod.data?.projects.find((item) => item.project_id === row.project_id)?.pod
    : [
        project.query.data?.project,
        ...(project.query.data?.pods ?? []),
        ...(project.query.data?.releases ?? []),
      ].find((item) => item?.scope_kind === row.scope_kind && item.scope_id === row.scope_id);
  if (!scope) return null;
  const title =
    row.scope_kind === "pod"
      ? `${row.name}'s date for ${row.project_name}`
      : `${row.name}: delivery date`;
  return <DeliveryDateDialog scope={scope} title={title} onClose={onClose} />;
}
