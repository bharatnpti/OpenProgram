import { useState, type ReactNode } from "react";

import type { DirectoryItemResponse, ScopeDeliveryResponse } from "../../api/schema";
import { useNames, usePods, useProjects } from "../../app/directory";
import { useRole } from "../../app/role";
import { useShownDay } from "../../app/viewingDate";
import { PanelState, SectionHeader } from "../../components/PanelState";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { formatDay } from "../../lib/format";
import { readState } from "../../lib/readState";
import type { BadgeTone } from "../../lib/status";
import { cn } from "../../lib/utils";
import { DeliveryDateDialog } from "../reports/DeliveryDateDialog";
import { Locked } from "../reports/ReportDialog";
import { useReportAccess } from "../reports/useReportAccess";
import { releaseName } from "./overallWords";
import { podChangeKeys, podDates } from "./podDates";
import { useDelivery, usePodDeliveries } from "./queries";
import { PhraseText } from "./SlipChart";
import { Fold, Legend, LineKey, Swatch, VizCard } from "./viz";

type Editing = { scope: ScopeDeliveryResponse; title: string } | null;

/** A line's colour on the track: the verdict's, grey when there is none to go by. */
const TONE_LINE: Record<BadgeTone, string> = {
  success: "var(--op-green)",
  warning: "var(--op-amber)",
  danger: "var(--op-red)",
  info: "var(--op-info)",
  neutral: "var(--op-grey-disabled)",
};

/**
 * O6, "Dates by pod", for the project's progress readers: each pod's own date
 * against the project's, from the project's delivery read. It replaces the
 * "Dates by pod" table and "How the pods' dates moved" (now folded under it).
 * Set date shows where the backend allows it: on every pod for a manager or an
 * admin, else on the pods the server says are the viewer's (`can_set_dates`).
 * A project's releases, when it has any, are drawn the same way below.
 */
export function PodDatesSection({
  projectId,
  releaseId,
}: {
  projectId: string;
  releaseId: string;
}) {
  const access = useReportAccess();
  const delivery = useDelivery(projectId);
  const pods = usePods();
  const [editing, setEditing] = useState<Editing>(null);
  const data = delivery.query.data;
  const managesPods = access.setPodDates && access.lens.some((r) => r === "mgr" || r === "admin");
  const podIds =
    access.setPodDates && !managesPods ? (data?.pods ?? []).map((p) => p.scope_id) : [];
  const podReads = usePodDeliveries(podIds);
  const runs = new Set(podIds.filter((_, index) => podReads[index]?.data?.can_set_dates));
  const lead = usePodLead(pods.data ?? []);
  const podDatesOffNow = access.pastDay("setPodDates");
  const datesOffNow = access.pastDay("setProjectDates");
  const project = data?.project;
  const button = (scope: ScopeDeliveryResponse, title: string) => (
    <Pill
      size="sm"
      variant="ghost"
      className="h-8 whitespace-nowrap px-3"
      aria-label={`${scope.commitment.target_date ? "Change date" : "Set date"}, ${scope.name}`}
      onClick={() => setEditing({ scope, title })}
    >
      {scope.commitment.target_date ? "Change date" : "Set date"}
    </Pill>
  );

  if (data && data.pods.length === 0 && data.releases.length === 0) return null;
  return (
    <section aria-labelledby="o6-h">
      <SectionHeader
        id="o6-h"
        title="Dates by pod"
        meta={project ? projectMeta(project.name, project.target) : undefined}
      />
      <PanelState
        isLoading={delivery.query.isLoading}
        error={delivery.query.error}
        onRetry={() => void delivery.query.refetch()}
      >
        {data && project ? (
          <VizCard>
            {data.pods.length > 0 ? (
              <DatesList
                label="Pod"
                scopes={data.pods}
                projectTarget={project.target}
                lead={(scope) => lead(scope.scope_id)}
                action={(scope) =>
                  access.setPodDates && (managesPods || runs.has(scope.scope_id))
                    ? button(scope, `${scope.name}'s date for ${project.name}`)
                    : null
                }
              />
            ) : (
              <p className="text-[13px] text-grey-body">No pod works on this project yet.</p>
            )}
            {podDatesOffNow && data.pods.length > 0 ? <Locked>{podDatesOffNow}</Locked> : null}
            {data.releases.length > 0 ? (
              <div className="grid gap-2">
                <h3 className="text-[15px] font-extrabold">Dates by release</h3>
                <DatesList
                  label="Release"
                  scopes={data.releases.map((item) => ({ ...item, name: releaseName(item.name) }))}
                  projectTarget={project.target}
                  current={releaseId}
                  action={(scope) =>
                    access.setProjectDates ? button(scope, `${scope.name}: delivery date`) : null
                  }
                />
                {datesOffNow ? <Locked>{datesOffNow}</Locked> : null}
              </div>
            ) : null}
            <DatesLegend project={Boolean(project.target)} />
            <PodChanges scopes={data.pods} />
          </VizCard>
        ) : null}
      </PanelState>
      {editing ? (
        <DeliveryDateDialog
          scope={editing.scope}
          title={editing.title}
          onClose={() => setEditing(null)}
        />
      ) : null}
    </section>
  );
}

/**
 * O6 for a scrum master, who cannot read the project's own forecast: each of
 * the project's pods from its pod read, with Set date on the pods the server
 * says are theirs. It replaces the three full pod strips: their Today leads with
 * their own pod's.
 */
export function ScrumMasterPodDates({ projectId }: { projectId: string }) {
  const { readOnly, reason } = useReportAccess();
  const projects = useProjects();
  const pods = usePods();
  const [editing, setEditing] = useState<Editing>(null);
  const project = projects.data?.find((item) => item.id === projectId);
  const projectPods = (pods.data ?? []).filter((pod) => project?.pod_ids.includes(pod.id));
  const reads = usePodDeliveries(projectPods.map((pod) => pod.id));
  const loaded = reads.every((read) => !read.isPending);
  const failed = reads.find((read) => read.error)?.error ?? null;
  const parts = reads.flatMap((read) =>
    (read.data?.projects ?? [])
      .filter((item) => item.project_id === projectId)
      .map((item) => ({ ...item, mine: Boolean(read.data?.can_set_dates) })),
  );
  const projectTarget = parts.find((item) => item.project_target)?.project_target ?? null;
  const mine = new Set(parts.filter((item) => item.mine).map((item) => item.pod.scope_id));
  const name = project?.name ?? "this project";

  return (
    <section aria-labelledby="o6-h">
      <SectionHeader id="o6-h" title="Dates by pod" meta={projectMeta(name, projectTarget)} />
      <PanelState
        {...readState({ isLoading: !loaded, isPending: !loaded, error: failed }, projects, pods)}
        isEmpty={projectPods.length === 0}
        emptyText="No pod works on this project yet."
      >
        <VizCard>
          <DatesList
            label="Pod"
            scopes={parts.map((item) => item.pod)}
            projectTarget={projectTarget}
            lead={(scope) => (mine.has(scope.scope_id) ? "yours" : null)}
            action={(scope) =>
              mine.has(scope.scope_id) && !readOnly ? (
                <Pill
                  size="sm"
                  variant="ghost"
                  className="h-8 whitespace-nowrap px-3"
                  aria-label={`${scope.commitment.target_date ? "Change date" : "Set date"}, ${scope.name}`}
                  onClick={() => setEditing({ scope, title: `${scope.name}'s date for ${name}` })}
                >
                  {scope.commitment.target_date ? "Change date" : "Set date"}
                </Pill>
              ) : null
            }
          />
          {readOnly && reason && mine.size > 0 ? <Locked>{reason}</Locked> : null}
          <DatesLegend project={Boolean(projectTarget)} />
          <PodChanges scopes={parts.map((item) => item.pod)} />
        </VizCard>
      </PanelState>
      {editing ? (
        <DeliveryDateDialog
          scope={editing.scope}
          title={editing.title}
          onClose={() => setEditing(null)}
        />
      ) : null}
    </section>
  );
}

function projectMeta(name: string, target: string | null): string {
  return target
    ? `Each pod's date for its part of ${name}, against the project's ${formatDay(target)}`
    : `Each pod's date for its part of ${name}; the project has no committed date yet`;
}

/**
 * The rows: name and verdict, a track from today with the pod's date on it and
 * the project's date as a mark, the fact in words under it, and the action. A
 * list, so each row reads as one line; the tracks are drawn for the eye only.
 */
function DatesList({
  label,
  scopes,
  projectTarget,
  current,
  lead,
  action,
}: {
  label: string;
  scopes: ScopeDeliveryResponse[];
  projectTarget: string | null;
  current?: string;
  lead?: (scope: ScopeDeliveryResponse) => string | null;
  action: (scope: ScopeDeliveryResponse) => ReactNode;
}) {
  // The tracks start on the day shown: today, or the past day being viewed.
  const today = useShownDay() ?? new Date().toLocaleDateString("en-CA");
  const dates = podDates(scopes, projectTarget, today);
  const grid =
    "grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1.5 @min-[700px]:grid-cols-[210px_minmax(0,1fr)_120px] @min-[700px]:gap-x-3.5";

  return (
    <div className="@container">
      <div
        className="grid grid-cols-[minmax(0,1fr)] pb-1 @min-[700px]:grid-cols-[210px_minmax(0,1fr)_120px] @min-[700px]:gap-x-3.5"
        aria-hidden
      >
        <span className="hidden text-[11px] font-bold uppercase tracking-wider text-grey-secondary @min-[700px]:block">
          {label}
        </span>
        <Scale dates={dates} projectTarget={projectTarget} />
        <span className="hidden @min-[700px]:block" />
      </div>
      <ul className="m-0 grid list-none p-0" aria-label={dates.spoken}>
        {dates.rows.map((row, index) => {
          const scope = scopes[index];
          const who = lead?.(scope);
          return (
            <li
              key={row.id}
              className={cn(
                grid,
                "items-center border-t border-(--op-viz-grid) py-3 [grid-template-areas:'name_act'_'track_track'_'fact_fact'] @min-[700px]:[grid-template-areas:'name_track_act'_'name_fact_act']",
                row.id === current && "bg-grey-header",
              )}
            >
              <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-[14px] font-extrabold [grid-area:name]">
                <span>{row.name}</span>
                {who ? (
                  <small className="text-[11px] font-bold text-grey-secondary">{who}</small>
                ) : null}
                <RagChip tone={row.chip.tone} className="h-[22px] px-2 text-[11.5px]">
                  {row.chip.label}
                </RagChip>
              </div>
              <div className="relative h-[18px] [grid-area:track]" aria-hidden>
                <span className="absolute -top-0.5 -bottom-0.5 left-0 border-l border-(--op-viz-axis)" />
                {row.at === null ? (
                  <span className="absolute inset-x-0 top-2 border-t-[1.5px] border-dashed border-(--op-viz-axis)" />
                ) : (
                  <>
                    <span
                      className="absolute left-0 top-[7px] h-1 rounded-sm"
                      style={{ width: `${row.at}%`, background: TONE_LINE[row.tone] }}
                    />
                    <span
                      className="absolute top-[3px] -ml-1.5 h-3 w-3 rounded-full border-2 border-(--op-viz-surface)"
                      style={{ left: `${row.at}%`, background: TONE_LINE[row.tone] }}
                    />
                  </>
                )}
                {dates.project !== null ? (
                  <span
                    className="absolute -top-0.5 -bottom-0.5 border-l-2 border-ink"
                    style={{ left: `${dates.project}%` }}
                  />
                ) : null}
              </div>
              <p className="text-[12.5px] text-grey-body [grid-area:fact]">{row.fact}</p>
              <div className="flex min-h-8 justify-end [grid-area:act]">{action(scope)}</div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** The axis over the tracks: Today at the left, month starts, and the project's date. */
function Scale({
  dates,
  projectTarget,
}: {
  dates: ReturnType<typeof podDates>;
  projectTarget: string | null;
}) {
  return (
    <div className="relative h-[18px] text-[11.5px] tabular-nums text-grey-secondary">
      <span className="absolute left-0 top-0 whitespace-nowrap">Today</span>
      {dates.ticks.map((tick) => (
        <span
          key={tick.label}
          className={cn(
            "absolute top-0 -translate-x-1/2 whitespace-nowrap",
            // On a narrow card a month near the project's label gives way to it.
            dates.project !== null &&
              Math.abs(tick.at - dates.project) < 25 &&
              "hidden @min-[700px]:inline",
          )}
          style={{ left: `${tick.at}%` }}
        >
          {tick.label}
        </span>
      ))}
      {dates.project !== null && projectTarget ? (
        <span
          className={cn(
            "absolute top-0 whitespace-nowrap font-extrabold text-ink",
            // Near the right edge the label ends at its mark instead of running past it.
            dates.project > 85 ? "-translate-x-full pl-1" : "-translate-x-1/2",
          )}
          style={{ left: `${dates.project}%` }}
        >
          <span className="@min-[700px]:hidden">Project</span>
          <span className="hidden @min-[700px]:inline">
            Project · {formatDay(projectTarget).replace(/^\w+ /, "")}
          </span>
        </span>
      ) : null}
    </div>
  );
}

function DatesLegend({ project }: { project: boolean }) {
  return (
    <Legend>
      {project ? (
        <span>
          <LineKey />
          Project date
        </span>
      ) : null}
      <span>
        <Swatch style={{ background: "var(--op-amber)" }} />A pod&apos;s date, in its verdict&apos;s
        colour
      </span>
      <span>
        <LineKey dashed />
        No date yet
      </span>
    </Legend>
  );
}

/** "How the pods' dates moved · 1": every pod's change, newest first, folded. */
function PodChanges({ scopes }: { scopes: ScopeDeliveryResponse[] }) {
  const count = scopes.reduce((sum, scope) => sum + scope.commitment.changes.length, 0);
  if (count === 0) return null;
  return (
    <Fold summary={`How the pods' dates moved · ${count}`}>
      <ol className="m-0 grid list-none gap-2 p-0 text-[13px] text-grey-body">
        {podChangeKeys(scopes).map((phrase, i) => (
          <li key={`${phrase[1]}-${i}`} className="flex items-start gap-2">
            <span
              aria-hidden
              className="mt-px inline-flex h-[18px] w-[18px] flex-none items-center justify-center rounded-full bg-ink text-[11px] font-bold text-(--op-viz-surface)"
            >
              {i + 1}
            </span>
            <PhraseText phrase={phrase} />
          </li>
        ))}
      </ol>
    </Fold>
  );
}

/**
 * Who runs each pod, for "Ira runs it": the pod's escalation scrum master when
 * set, else the member the roster gives the scrum master role. Nobody named, no
 * line: never an id.
 */
function usePodLead(pods: DirectoryItemResponse[]): (podId: string) => string | null {
  const { people } = useRole();
  const names = useNames();
  return (podId) => {
    const pod = pods.find((item) => item.id === podId);
    if (!pod) return null;
    const lead = pod.metadata?.escalation_sm_member_id;
    const id =
      typeof lead === "string" && lead
        ? lead
        : pod.member_ids.find((member) =>
            people.some((person) => person.id === member && person.roles.includes("sm")),
          );
    if (!id || !names.known(id)) return null;
    return `${names(id).split(/\s+/)[0]} runs it`;
  };
}
