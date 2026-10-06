import type { ScopeDeliveryResponse } from "../../api/schema";
import { PanelState, SectionHeader, TableBox, td, th } from "../../components/PanelState";
import { Card } from "../../components/ui/Card";
import { RagChip } from "../../components/ui/RagChip";
import { formatDate, formatDay, progressWidth } from "../../lib/format";
import { VERDICT_LABELS, toneForVerdict } from "../../lib/status";
import { Burndown } from "./Burndown";
import type { Marker } from "./charts";
import { PROJECT_PROGRESS_READERS, useDelivery, useRequirements } from "./queries";

/** The committed date, whether it will hold, why, and the burn-down behind it. */
export function ForecastSection({ projectId }: { projectId: string }) {
  const delivery = useDelivery(projectId);
  const requirements = useRequirements(projectId);
  const project = delivery.query.data?.project;

  return (
    <section>
      <SectionHeader
        title="Delivery date and forecast"
        meta={project ? commitmentLine(project) : undefined}
      />
      <PanelState
        locked={delivery.locked}
        needs={PROJECT_PROGRESS_READERS}
        isLoading={delivery.query.isLoading}
        error={delivery.query.error}
        onRetry={() => void delivery.query.refetch()}
      >
        {project ? (
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
            <Verdict scope={project} />
            <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
              <Card padding="p-5">
                <h3 className="text-[15px] font-extrabold">Burn-down</h3>
                <p className="mb-3 text-[12px] text-grey-secondary">
                  Requirements not yet in production, by count
                </p>
                {requirements.query.data ? (
                  <Burndown
                    timeline={requirements.query.data.timeline}
                    markers={markersFor(project)}
                  />
                ) : (
                  <p className="text-[13px] text-grey-secondary">Loading…</p>
                )}
              </Card>
              <Card padding="p-5" className="grid grid-cols-[minmax(0,1fr)] content-start gap-4">
                <Completion
                  percent={requirements.query.data?.percent_complete ?? null}
                  hasPoints={requirements.query.data?.has_points ?? false}
                  done={requirements.query.data?.done ?? 0}
                  total={requirements.query.data?.total ?? 0}
                />
                <TwoAnswers scope={project} />
              </Card>
            </div>
            {delivery.query.data && delivery.query.data.pods.length > 0 ? (
              <ScopeTable title="Dates by pod" scopes={delivery.query.data.pods} />
            ) : null}
            {delivery.query.data && delivery.query.data.releases.length > 0 ? (
              <ScopeTable title="Dates by release" scopes={delivery.query.data.releases} />
            ) : null}
            {project.commitment.changes.length > 0 ? <DateChanges scope={project} /> : null}
          </div>
        ) : null}
      </PanelState>
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
  const tone = toneForVerdict(scope.verdict);
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
        {VERDICT_LABELS[scope.verdict]}
        {scope.target ? ` for ${formatDate(scope.target)}` : ""}
      </p>
      {scope.reasons.length > 0 ? (
        <ul className="mt-2 grid list-disc gap-1 pl-5 text-[14px] font-medium">
          {scope.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      ) : null}
      <p className="mt-2 text-[13px] opacity-80">
        {scope.open} of {scope.total} still open
      </p>
    </div>
  );
}

function Completion({
  percent,
  hasPoints,
  done,
  total,
}: {
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
        <span className="ml-2 text-[12px] font-bold text-grey-secondary">
          {hasPoints ? "by story points" : "by count"}
        </span>
      </p>
      <div className="mt-2 h-2.5 overflow-hidden rounded-full bg-grey-fill">
        <div
          className="h-full rounded-full bg-magenta"
          style={{ width: `${progressWidth(percent)}%` }}
        />
      </div>
      <p className="mt-2 text-[12px] text-grey-secondary">
        {done} of {total} requirements in production
      </p>
    </div>
  );
}

function TwoAnswers({ scope }: { scope: ScopeDeliveryResponse }) {
  const h = scope.history;
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-2">
      <div className="rounded-2xl border border-grey-border p-3">
        <p className="text-[11px] font-bold uppercase tracking-wider text-grey-secondary">
          Completion rate says
        </p>
        {h.p50 ? (
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
              latest of {scope.team.dated} dated items
              {scope.team.latest_key ? ` (${scope.team.latest_key})` : ""}
              {scope.team.undated > 0 ? ` · ${scope.team.undated} without a date` : ""}
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

function ScopeTable({ title, scopes }: { title: string; scopes: ScopeDeliveryResponse[] }) {
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
            </tr>
          </thead>
          <tbody>
            {scopes.map((scope) => (
              <tr key={scope.scope_id}>
                <td className={`${td} font-bold`}>{scope.name}</td>
                <td className={`${td} whitespace-nowrap`}>{formatDate(scope.target)}</td>
                <td className={td}>
                  {scope.total === 0 ? (
                    <RagChip tone="neutral" className="h-6 px-2.5 text-[12px]">
                      Nothing in scope
                    </RagChip>
                  ) : (
                    <RagChip
                      tone={toneForVerdict(scope.verdict)}
                      className="h-6 px-2.5 text-[12px]"
                    >
                      {VERDICT_LABELS[scope.verdict]}
                    </RagChip>
                  )}
                </td>
                <td className={`${td} text-grey-body`}>
                  {scope.total === 0
                    ? "No requirements are counted for it yet."
                    : scope.reasons.join(" ") || "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableBox>
    </div>
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
            {scope.commitment.changes.map((change) => (
              <tr key={change.changed_at}>
                <td className={`${td} whitespace-nowrap`}>{formatDate(change.changed_at)}</td>
                <td className={`${td} whitespace-nowrap`}>{formatDate(change.target_date)}</td>
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
