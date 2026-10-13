import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { apiClient } from "../../api/client";
import type { Measure, PeriodKind } from "../../api/schema";
import { useRole } from "../../app/role";
import { PanelState, SectionHeader } from "../../components/PanelState";
import { Fold } from "../../components/viz/Fold";
import { readState } from "../../lib/readState";
import { DeliveryDateDialog } from "../reports/DeliveryDateDialog";
import { Locked } from "../reports/ReportDialog";
import { useReportAccess } from "../reports/useReportAccess";
import { PlanningCompleteDialog } from "./BaselineSection";
import { ImpactWaterfall } from "./ImpactWaterfall";
import { useBaselines, useDelivery, useImpact, useTimeline } from "./queries";
import { BandOverTime } from "./SlipChart";
import { TimelineChart } from "./TimelineChart";
import { type Row } from "./timeline";
import { VizCard } from "./viz";

/**
 * Overall's Timeline (DESIGN §5, simplified by D78): one row each for the
 * project (or the release picked above), its releases and its pods, from
 * planning to the committed date, with what is done, the likely finish and a
 * dot on each day something changed; then why the picked row's date moved, with
 * its period chips; then, folded, how the likely date moved day by day. The
 * server words every row for the reader: a scrum master gets the pods only,
 * their own whole and the others' dates. `?row=` picks the row explained.
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
        meta="Each row from planning to its committed date: what is done, when it will likely finish, and what changed"
      />
      <PanelState {...readState(timeline.query)} onRetry={() => void timeline.query.refetch()}>
        {data ? (
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
            <VizCard>
              <TimelineChart
                data={data}
                selected={selected?.row}
                onSelect={pick}
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
            {selected ? (
              <ImpactWaterfall
                row={selected}
                read={impact.query}
                measure={measure}
                onMeasure={setMeasure}
                period={effective}
                planned={Boolean(selected.baseline)}
                onPeriod={setPeriod}
                onPlan={
                  !selected.baseline && access.setProjectDates && baselines.query.data
                    ? () => setPlanning(true)
                    : undefined
                }
              />
            ) : null}
            {head && head.access === "panel" ? (
              <Fold summary="How the likely date moved, day by day">
                <BandOverTime projectId={projectId} releaseId={releaseId} />
              </Fold>
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
