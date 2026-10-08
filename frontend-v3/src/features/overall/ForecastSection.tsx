import { useState } from "react";

import type { ScopeDeliveryResponse } from "../../api/schema";
import { useProjects } from "../../app/directory";
import { useRole } from "../../app/role";
import { useShownDay } from "../../app/viewingDate";
import { PanelState, SectionHeader, TableBox, td, th } from "../../components/PanelState";
import { Card } from "../../components/ui/Card";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { formatDate, formatDay, progressWidth } from "../../lib/format";
import { VERDICT_LABELS, toneForVerdict } from "../../lib/status";
import { PodProjectRow } from "../delivery/PodDeliveryCard";
import { DeliveryDateDialog } from "../reports/DeliveryDateDialog";
import { Locked } from "../reports/ReportDialog";
import { useReportAccess } from "../reports/useReportAccess";
import { Burndown } from "./Burndown";
import { burndownSeries, type Marker } from "./charts";
import {
  inScope,
  isPastDay,
  reasonsAfterCause,
  releaseName,
  teamDatesLine,
  verdictCause,
} from "./overallWords";
import { useDelivery, usePodDeliveries, useRequirements } from "./queries";

type Editing = { scope: ScopeDeliveryResponse; title: string } | null;

/**
 * The committed date, whether it will hold, why, and the burn-down behind it,
 * for the whole project or the release picked above, for the project's
 * progress readers. The product owner or a manager commits the project's and a
 * release's date; a pod's part is its scrum master's, who reads only the dates
 * of the project's pods here. A developer gets neither.
 */
export function ForecastSection({
  projectId,
  releaseId = "",
}: {
  projectId: string;
  releaseId?: string;
}) {
  const { access } = useRole();
  if (access.overall.forecast)
    return <ProjectForecast projectId={projectId} releaseId={releaseId} />;
  return access.overall.podDates ? <PodDatesForScrumMaster projectId={projectId} /> : null;
}

function ProjectForecast({ projectId, releaseId }: { projectId: string; releaseId: string }) {
  const access = useReportAccess();
  const delivery = useDelivery(projectId);
  const requirements = useRequirements(projectId, releaseId || undefined);
  const [editing, setEditing] = useState<Editing>(null);
  const data = delivery.query.data;
  const release = releaseId
    ? data?.releases.find((item) => item.scope_id === releaseId)
    : undefined;
  const scope = release ?? data?.project;
  const edit = (item: ScopeDeliveryResponse, title: string) => setEditing({ scope: item, title });
  const timeline = requirements.query.data?.timeline;
  const series = timeline ? burndownSeries(timeline) : null;
  // The backend sets any pod's date for a manager or admin; anyone else only
  // for a pod they run (`can_set_dates` per pod). Under real sign-in a product
  // owner who is also a scrum master reads this table, so the rows they may
  // change come from each pod's own answer, not from the scrum master role.
  const managesPods = access.setPodDates && access.lens.some((r) => r === "mgr" || r === "admin");
  const podIds =
    access.setPodDates && !managesPods ? (data?.pods ?? []).map((p) => p.scope_id) : [];
  const podReads = usePodDeliveries(podIds);
  const runs = new Set(podIds.filter((_, index) => podReads[index]?.data?.can_set_dates));
  // A past day switches the buttons off; who could use them today is told why.
  const datesOffNow = access.pastDay("setProjectDates");
  const podDatesOffNow = access.pastDay("setPodDates");

  return (
    <section>
      <SectionHeader
        title={
          release
            ? `Delivery date and forecast: ${releaseName(release.name)}`
            : "Delivery date and forecast"
        }
        meta={scope ? commitmentLine(scope) : undefined}
        actions={
          scope && access.setProjectDates ? (
            <Pill
              size="sm"
              variant="ghost"
              onClick={() =>
                edit(
                  scope,
                  release
                    ? `${releaseName(release.name)}: delivery date`
                    : `${scope.name}: delivery date`,
                )
              }
            >
              {scope.commitment.target_date ? "Change date" : "Set date"}
            </Pill>
          ) : scope && datesOffNow ? (
            <Locked>{datesOffNow}</Locked>
          ) : undefined
        }
      />
      <PanelState
        isLoading={delivery.query.isLoading}
        error={delivery.query.error}
        onRetry={() => void delivery.query.refetch()}
      >
        {data && scope ? (
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
            <Verdict scope={scope} />
            <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
              <Card padding="p-5">
                <h3 className="text-[15px] font-extrabold">Burn-down</h3>
                <p className="mb-3 text-[12px] text-grey-secondary">
                  {series?.caption ??
                    (requirements.query.isSuccess ? "Not yet in production" : null)}
                </p>
                {series ? (
                  <Burndown series={series} markers={markersFor(scope)} />
                ) : (
                  <PanelState
                    isLoading={requirements.query.isLoading}
                    error={requirements.query.error}
                    onRetry={() => void requirements.query.refetch()}
                    isEmpty
                    emptyText="No requirements are counted yet, so there is nothing to burn down."
                  >
                    {null}
                  </PanelState>
                )}
              </Card>
              <Card padding="p-5" className="grid grid-cols-[minmax(0,1fr)] content-start gap-4">
                <Completion
                  loading={requirements.query.isLoading}
                  unread={Boolean(requirements.query.error)}
                  percent={requirements.query.data?.percent_complete ?? null}
                  hasPoints={requirements.query.data?.has_points ?? false}
                  done={requirements.query.data?.done ?? 0}
                  total={requirements.query.data?.total ?? 0}
                />
                <TwoAnswers scope={scope} />
              </Card>
            </div>
            {data.pods.length > 0 ? (
              <ScopeTable
                title="Dates by pod"
                scopes={data.pods}
                onEdit={
                  access.setPodDates
                    ? (pod) => edit(pod, `${pod.name}'s date for ${data.project.name}`)
                    : undefined
                }
                canEdit={managesPods ? undefined : (pod) => runs.has(pod.scope_id)}
                locked={podDatesOffNow ?? undefined}
              />
            ) : null}
            {data.releases.length > 0 ? (
              <ScopeTable
                title="Dates by release"
                scopes={data.releases}
                current={releaseId}
                onEdit={
                  access.setProjectDates
                    ? (item) => edit(item, `${releaseName(item.name)}: delivery date`)
                    : undefined
                }
                locked={datesOffNow ?? undefined}
              />
            ) : null}
            {scope.commitment.changes.length > 0 ? <DateChanges scope={scope} /> : null}
            {data.pods.some((pod) => pod.commitment.changes.length > 0) ? (
              <PodDateChanges pods={data.pods} />
            ) : null}
          </div>
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
 * A scrum master cannot read the project's forecast, but does commit the date
 * of the pods they run: each of the project's pods, read pod by pod, with the
 * button where the server says the pod is theirs.
 */
function PodDatesForScrumMaster({ projectId }: { projectId: string }) {
  const { readOnly, reason } = useReportAccess();
  const projects = useProjects();
  const project = projects.data?.find((item) => item.id === projectId);
  const podIds = project?.pod_ids ?? [];
  const pods = usePodDeliveries(podIds);
  const [editing, setEditing] = useState<{ scope: ScopeDeliveryResponse; title: string } | null>(
    null,
  );
  const rows = pods.flatMap((query) => {
    const item = query.data?.projects.find((entry) => entry.project_id === projectId);
    return item && query.data
      ? [{ item, runs: query.data.can_set_dates, canSet: query.data.can_set_dates && !readOnly }]
      : [];
  });
  const loading = projects.isLoading || pods.some((query) => query.isLoading);
  const error = pods.find((query) => query.error)?.error ?? null;

  return (
    <section>
      <SectionHeader
        title="Dates by pod"
        meta="Each pod's date for its part of this project, and the forecast behind it"
      />
      <PanelState
        isLoading={loading}
        error={error}
        isEmpty={rows.length === 0}
        emptyText="No pod works on this project yet."
      >
        <ul className="grid grid-cols-[minmax(0,1fr)] gap-3 lg:grid-cols-2">
          {rows.map(({ item, canSet }) => (
            <PodProjectRow
              key={item.pod.scope_id}
              title={item.pod.name}
              item={item}
              canSet={canSet}
              onEdit={() =>
                setEditing({
                  scope: item.pod,
                  title: `${item.pod.name}'s date for ${item.project_name}`,
                })
              }
            />
          ))}
        </ul>
        {readOnly && reason && rows.some((row) => row.runs) ? (
          <Locked className="mt-2">{reason}</Locked>
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

function commitmentLine(scope: ScopeDeliveryResponse): string {
  const c = scope.commitment;
  if (!scope.target) return "No committed date yet.";
  const source = scope.target_source === "jira_release" ? "from the Jira release" : "committed";
  const moved =
    c.times_moved > 0
      ? ` · moved ${c.times_moved}×${c.moved_days ? `, ${c.moved_days > 0 ? "+" : ""}${c.moved_days} days` : ""}`
      : "";
  const jira =
    scope.jira_release_date && scope.jira_release_date !== scope.target
      ? ` · Jira release date ${formatDate(scope.jira_release_date)}`
      : "";
  return `${formatDate(scope.target)} ${source}${moved}${jira}`;
}

function Verdict({ scope }: { scope: ScopeDeliveryResponse }) {
  const tone = inScope(scope) ? toneForVerdict(scope.verdict) : "neutral";
  const cause = inScope(scope) ? verdictCause(scope) : null;
  const reasons = reasonsAfterCause(scope.reasons, cause);
  const bg =
    tone === "danger"
      ? "bg-rag-red-bg text-rag-red"
      : tone === "warning"
        ? "bg-rag-amber-bg text-rag-amber"
        : tone === "success"
          ? "bg-rag-green-bg text-rag-green"
          : "bg-grey-fill text-grey-body";
  return (
    <div className={`rounded-3xl p-5 ${bg}`}>
      <p className="text-[20px] font-extrabold">
        {inScope(scope) ? VERDICT_LABELS[scope.verdict] : "Nothing in scope yet"}
        {scope.target ? ` for ${formatDate(scope.target)}` : ""}
      </p>
      {cause ? <p className="mt-2 text-[15px] font-bold">{cause.because}</p> : null}
      {cause?.also ? <p className="mt-1 text-[13px] font-medium">{cause.also}</p> : null}
      {inScope(scope) && reasons.length > 0 ? (
        <ul className="mt-2 grid list-disc gap-1 pl-5 text-[14px] font-medium">
          {reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      ) : null}
      <p className="mt-2 text-[13px] opacity-80">
        {inScope(scope)
          ? `${scope.open} of ${scope.total} still open`
          : "No requirements are counted for it yet, so there is nothing to forecast."}
      </p>
    </div>
  );
}

function Completion({
  loading,
  unread,
  percent,
  hasPoints,
  done,
  total,
}: {
  /** The requirements are being read: "0 of 0 in production" is not what they say yet. */
  loading: boolean;
  unread: boolean;
  percent: number | null;
  hasPoints: boolean;
  done: number;
  total: number;
}) {
  return (
    <div>
      <p className="text-[11px] font-bold uppercase tracking-wider text-grey-secondary">
        Completion
      </p>
      <p className="mt-1 text-[26px] font-extrabold tabular-nums">
        {percent === null ? "—" : `${Math.round(percent)}%`}
        {loading ? null : (
          <span className="ml-2 text-[12px] font-bold text-grey-secondary">
            {hasPoints ? "by story points" : "by count"}
          </span>
        )}
      </p>
      <div className="mt-2 h-2.5 overflow-hidden rounded-full bg-grey-fill">
        <div
          className="h-full rounded-full bg-magenta"
          style={{ width: `${progressWidth(percent)}%` }}
        />
      </div>
      <p className="mt-2 text-[12px] text-grey-secondary">
        {loading
          ? "Loading the requirements…"
          : unread
            ? "The requirements could not be read."
            : `${done} of ${total} requirements in production`}
      </p>
    </div>
  );
}

function TwoAnswers({ scope }: { scope: ScopeDeliveryResponse }) {
  const h = scope.history;
  const shownDay = useShownDay();
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-2">
      <div className="rounded-2xl border border-grey-border p-3">
        <p className="text-[11px] font-bold uppercase tracking-wider text-grey-secondary">
          Completion rate says
        </p>
        {!inScope(scope) ? (
          <p className="mt-1 text-[13px] text-grey-body">Nothing to forecast yet.</p>
        ) : h.p50 ? (
          <>
            <p className="mt-1 text-[18px] font-extrabold">{formatDay(h.p50)}</p>
            <p className="text-[12px] text-grey-secondary">
              50% likely · {h.p85 ? `85% likely ${formatDay(h.p85)}` : "no 85% date"} · from{" "}
              {h.sample_days} days of history
            </p>
          </>
        ) : (
          <p className="mt-1 text-[13px] text-grey-body">{h.reason ?? "Not enough history yet."}</p>
        )}
      </div>
      <div className="rounded-2xl border border-grey-border p-3">
        <p className="text-[11px] font-bold uppercase tracking-wider text-grey-secondary">
          The team says
        </p>
        {scope.team.latest ? (
          <>
            <p className="mt-1 text-[18px] font-extrabold">{formatDay(scope.team.latest)}</p>
            <p className="text-[12px] text-grey-secondary">
              {teamDatesLine(scope.team, isPastDay(scope.team.latest, shownDay))}
            </p>
          </>
        ) : (
          <p className="mt-1 text-[13px] text-grey-body">
            No check-in estimates or Jira due dates.
          </p>
        )}
      </div>
    </div>
  );
}

function markersFor(scope: ScopeDeliveryResponse): Marker[] {
  const markers: Marker[] = [];
  if (scope.target) markers.push({ key: "committed", day: scope.target, label: "Committed" });
  if (scope.history.p50) markers.push({ key: "p50", day: scope.history.p50, label: "50% likely" });
  if (scope.history.p85) markers.push({ key: "p85", day: scope.history.p85, label: "85% likely" });
  if (scope.team.latest)
    markers.push({ key: "team", day: scope.team.latest, label: "Team's dates" });
  return markers;
}

function ScopeTable({
  title,
  scopes,
  current,
  onEdit,
  canEdit,
  locked,
}: {
  title: string;
  scopes: ScopeDeliveryResponse[];
  current?: string;
  onEdit?: (scope: ScopeDeliveryResponse) => void;
  /** Which rows `onEdit` is offered on; every row when left out. */
  canEdit?: (scope: ScopeDeliveryResponse) => boolean;
  locked?: string;
}) {
  return (
    <div>
      <h3 className="mb-2 text-[15px] font-extrabold">{title}</h3>
      <TableBox>
        <table className="w-full min-w-[620px] border-collapse">
          <thead>
            <tr>
              <th className={th}>Name</th>
              <th className={th}>Date</th>
              <th className={th}>Verdict</th>
              <th className={th}>Why</th>
              {onEdit ? (
                // Named by aria-label: a visually hidden child is positioned
                // outside the table's scroll box and widens a phone's page.
                <th className={th} aria-label="Set the date" />
              ) : null}
            </tr>
          </thead>
          <tbody>
            {scopes.map((scope) => (
              <tr
                key={scope.scope_id}
                className={scope.scope_id === current ? "bg-grey-header" : undefined}
              >
                <td className={`${td} font-bold`}>{scope.name}</td>
                <td className={`${td} whitespace-nowrap`}>
                  {formatDate(scope.target)}
                  {scope.target_source === "jira_release" ? (
                    <span className="ml-1 text-[11px] text-grey-secondary">from Jira</span>
                  ) : null}
                </td>
                <td className={`${td} whitespace-nowrap`}>
                  {inScope(scope) ? (
                    <RagChip
                      tone={toneForVerdict(scope.verdict)}
                      className="h-6 px-2.5 text-[12px]"
                    >
                      {VERDICT_LABELS[scope.verdict]}
                    </RagChip>
                  ) : (
                    <RagChip tone="neutral" className="h-6 px-2.5 text-[12px]">
                      Nothing in scope
                    </RagChip>
                  )}
                </td>
                <td className={`${td} text-grey-body`}>
                  <WhyCell scope={scope} />
                </td>
                {onEdit ? (
                  <td className={td}>
                    {canEdit === undefined || canEdit(scope) ? (
                      <Pill
                        size="sm"
                        variant="ghost"
                        className="h-8 whitespace-nowrap px-3"
                        aria-label={`${scope.commitment.target_date ? "Change" : "Set"} ${scope.name}'s date`}
                        onClick={() => onEdit(scope)}
                      >
                        {scope.commitment.target_date ? "Change date" : "Set date"}
                      </Pill>
                    ) : null}
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      </TableBox>
      {locked ? <Locked className="mt-2">{locked}</Locked> : null}
    </div>
  );
}

/** A scope's cause first, in bold, then the server's reasons without the line the cause says. */
function WhyCell({ scope }: { scope: ScopeDeliveryResponse }) {
  if (!inScope(scope)) return <>No requirements are counted for it yet.</>;
  const cause = verdictCause(scope);
  const reasons = reasonsAfterCause(scope.reasons, cause).join(" ");
  return (
    <>
      {cause ? <span className="block font-bold text-ink">{cause.because}</span> : null}
      {reasons || (cause ? null : "—")}
    </>
  );
}

function DateChanges({ scope }: { scope: ScopeDeliveryResponse }) {
  return (
    <div>
      <h3 className="mb-2 text-[15px] font-extrabold">How the date moved</h3>
      <TableBox>
        <table className="w-full min-w-[560px] border-collapse">
          <thead>
            <tr>
              <th className={th}>When</th>
              <th className={th}>New date</th>
              <th className={th}>Who</th>
              <th className={th}>Why</th>
            </tr>
          </thead>
          <tbody>
            {[...scope.commitment.changes].reverse().map((change) => (
              <tr key={change.changed_at}>
                <td className={`${td} whitespace-nowrap`}>{formatDate(change.changed_at)}</td>
                <td className={`${td} whitespace-nowrap`}>
                  {change.target_date ? formatDate(change.target_date) : "Cleared"}
                </td>
                <td className={td}>{change.changed_by_name}</td>
                <td className={`${td} text-grey-body`}>{change.note || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableBox>
    </div>
  );
}

/** The same history for each pod's part of the project, one pod to a row, newest first. */
function PodDateChanges({ pods }: { pods: ScopeDeliveryResponse[] }) {
  const rows = pods
    .flatMap((pod) => pod.commitment.changes.map((change) => ({ pod, change })))
    .sort((a, b) => b.change.changed_at.localeCompare(a.change.changed_at));
  return (
    <div>
      <h3 className="mb-2 text-[15px] font-extrabold">How the pods&apos; dates moved</h3>
      <TableBox>
        <table className="w-full min-w-[620px] border-collapse">
          <thead>
            <tr>
              <th className={th}>When</th>
              <th className={th}>Pod</th>
              <th className={th}>New date</th>
              <th className={th}>Who</th>
              <th className={th}>Why</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ pod, change }) => (
              <tr key={`${pod.scope_id}-${change.changed_at}`}>
                <td className={`${td} whitespace-nowrap`}>{formatDate(change.changed_at)}</td>
                <td className={`${td} font-bold`}>{pod.name}</td>
                <td className={`${td} whitespace-nowrap`}>
                  {change.target_date ? formatDate(change.target_date) : "Cleared"}
                </td>
                <td className={td}>{change.changed_by_name}</td>
                <td className={`${td} text-grey-body`}>{change.note || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableBox>
    </div>
  );
}
