import { useQuery } from "@tanstack/react-query";
import { CircleAlert } from "lucide-react";
import { useState, type ReactNode } from "react";

import { apiClient } from "../../api/client";
import type { DeliveryStage, RequirementsResponse } from "../../api/schema";
import { Card } from "../../components/ui/Card";
import { cn } from "../../lib/utils";
import { StageTimeline } from "./StageTimeline";
import { STAGE_COLORS, STAGE_LABELS, STAGES, changeLabel, percentLabel } from "./stages";

const LIST_LIMIT = 12;

/**
 * A project's requirements by delivery stage: how complete it is, where each
 * requirement sits, and how the stages moved over the last 30 days.
 *
 * Stages are read from tracker statuses through the admin's stage mapping, and
 * each day's counts come from that day's stored snapshot, so an earlier day
 * shows what was true then.
 */
export function RequirementsCard({ projectId, asOf }: { projectId: string; asOf: string }) {
  const [releaseId, setReleaseId] = useState("");
  const releases = useQuery({
    queryKey: ["persona", "releases", projectId],
    queryFn: () => apiClient.releases(projectId),
  });
  const requirements = useQuery({
    queryKey: ["persona", "requirements", projectId, asOf, releaseId],
    queryFn: () =>
      apiClient.projectRequirements(projectId, asOf, undefined, releaseId || undefined),
    placeholderData: (previous) => previous,
  });
  const scopeSwitch =
    (releases.data ?? []).length > 0 ? (
      <select
        aria-label="Which requirements"
        className="rounded-full border border-grey-border bg-white px-3 py-1.5 text-[13px] font-bold"
        value={releaseId}
        onChange={(event) => setReleaseId(event.target.value)}
      >
        <option value="">Whole project</option>
        {(releases.data ?? []).map((release) => (
          <option key={release.release_id} value={release.release_id}>
            {release.name}
          </option>
        ))}
      </select>
    ) : null;

  if (requirements.isError) {
    return (
      <Card padding="p-6">
        <h3 className="text-[18px] font-bold">Requirements</h3>
        <p className="mt-2 text-[14px] text-rag-red">Requirements could not be loaded.</p>
      </Card>
    );
  }
  if (!requirements.data) {
    return (
      <Card padding="p-6">
        <h3 className="text-[18px] font-bold">Requirements</h3>
        <p className="mt-2 text-[14px] text-grey-secondary">Counting requirements…</p>
      </Card>
    );
  }
  return <RequirementsBody data={requirements.data} scopeSwitch={scopeSwitch} />;
}

function RequirementsBody({
  data,
  scopeSwitch,
}: {
  data: RequirementsResponse;
  scopeSwitch: ReactNode;
}) {
  const [showAll, setShowAll] = useState(false);
  if (!data.available) {
    return (
      <Card padding="p-6">
        <h3 className="text-[18px] font-bold">Requirements</h3>
        <p className="mt-2 text-[14px] text-grey-secondary">
          No snapshot of this project&apos;s requirements was kept on {dayLabel(data.as_of)}.
        </p>
      </Card>
    );
  }
  const percent = data.percent_complete;
  const listed = showAll ? data.requirements : data.requirements.slice(0, LIST_LIMIT);

  return (
    <Card padding="p-6" className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h3 className="text-[18px] font-bold">Requirements</h3>
            {scopeSwitch}
          </div>
          <p className="mt-0.5 text-[13px] text-grey-secondary">
            {data.release_name ? `${data.release_name}, by delivery stage` : "By delivery stage"}
            {data.live ? ", as the tracker shows them now" : `, on ${dayLabel(data.as_of)}`}.
          </p>
        </div>
        <div className="min-w-[240px] flex-1 sm:max-w-[360px]">
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-[28px] font-extrabold">{percentLabel(percent)}</span>
            <span className="text-[13px] text-grey-secondary">{completionText(data)}</span>
          </div>
          <div
            className="mt-1.5 h-2.5 overflow-hidden rounded-full bg-magenta-tint"
            role="meter"
            aria-label="Share of requirements in production"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(percent ?? 0)}
          >
            <div
              className="h-full rounded-full bg-magenta"
              style={{ width: `${Math.max(0, Math.min(100, percent ?? 0))}%` }}
            />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
        {STAGES.map((stage) => {
          const item = data.stages.find((entry) => entry.stage === stage);
          const change = changeLabel(item?.change);
          return (
            <div key={stage} className="rounded-2xl bg-grey-fill px-4 py-3">
              <div className="flex items-center gap-1.5 text-[12px] font-bold text-grey-secondary">
                <span
                  aria-hidden
                  className="inline-block h-2.5 w-2.5 rounded-[3px]"
                  style={{ backgroundColor: STAGE_COLORS[stage] }}
                />
                {STAGE_LABELS[stage]}
              </div>
              <div className="mt-1 flex items-baseline gap-2">
                <span className="text-[24px] font-extrabold">{item?.count ?? 0}</span>
                {change ? (
                  <span className="text-[12px] font-bold text-grey-secondary">{change}</span>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>

      {data.unmapped_statuses.length > 0 ? (
        <div className="flex items-start gap-2 rounded-2xl bg-rag-amber-bg px-4 py-3 text-[13px] text-rag-amber-deep">
          <CircleAlert size={14} className="mt-0.5 shrink-0" />
          <span>
            Counted by their broad state because no stage names them yet:{" "}
            <strong>{data.unmapped_statuses.join(", ")}</strong>. An admin places them under
            Configuration, Delivery stages.
          </span>
        </div>
      ) : null}

      <div>
        <h4 className="text-[15px] font-bold">Over time</h4>
        {data.timeline.length < 2 ? (
          <p className="mt-1 text-[13px] text-grey-secondary">
            The timeline fills in as a snapshot is kept each day
            {data.timeline.length === 1 ? `; the first is ${dayLabel(data.timeline[0].day)}` : ""}.
          </p>
        ) : (
          <div className="mt-2">
            <StageTimeline points={data.timeline} />
          </div>
        )}
      </div>

      {data.previous_day ? (
        <div>
          <h4 className="text-[15px] font-bold">Moved since {dayLabel(data.previous_day)}</h4>
          {data.moves.length === 0 ? (
            <p className="mt-1 text-[13px] text-grey-secondary">No requirement changed stage.</p>
          ) : (
            <ul className="mt-2 flex flex-col gap-1.5 text-[14px]">
              {data.moves.slice(0, 10).map((move) => (
                <li key={move.key} className="flex flex-wrap items-center gap-x-2">
                  <span className="font-bold">{move.key}</span>
                  <span className="text-grey-body">{move.title}</span>
                  <span className="text-[13px] text-grey-secondary">
                    {moveText(move.from_stage, move.to_stage)}
                  </span>
                </li>
              ))}
              {data.moves.length > 10 ? (
                <li className="text-[13px] text-grey-secondary">
                  and {data.moves.length - 10} more
                </li>
              ) : null}
            </ul>
          )}
        </div>
      ) : null}

      {data.requirements.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-[14px]">
            <thead>
              <tr className="border-b border-grey-border text-left text-[12px] uppercase tracking-wide text-grey-secondary">
                <th className="py-2 pr-3 font-bold">Requirement</th>
                <th className="px-3 py-2 font-bold">Stage</th>
                <th className="px-3 py-2 font-bold">Status</th>
                <th className="px-3 py-2 font-bold">Assignee</th>
                <th className="py-2 pl-3 font-bold">In stage since</th>
              </tr>
            </thead>
            <tbody>
              {listed.map((item) => (
                <tr key={item.key} className="border-b border-grey-border last:border-0">
                  <td className="py-2 pr-3">
                    <span className="font-bold">{item.key}</span>{" "}
                    <span className="text-grey-body">{item.title}</span>
                  </td>
                  <td className="px-3 py-2">
                    <span className="flex items-center gap-1.5 whitespace-nowrap">
                      <span
                        aria-hidden
                        className="inline-block h-2.5 w-2.5 rounded-[3px]"
                        style={{ backgroundColor: STAGE_COLORS[item.stage] }}
                      />
                      {STAGE_LABELS[item.stage]}
                    </span>
                  </td>
                  <td className={cn("px-3 py-2", !item.mapped && "text-rag-amber")}>
                    {item.status ?? "—"}
                  </td>
                  <td className="px-3 py-2">{item.assignee_name ?? "—"}</td>
                  <td className="py-2 pl-3 whitespace-nowrap">
                    {item.in_stage_since ? dayLabel(item.in_stage_since) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.requirements.length > LIST_LIMIT ? (
            <button
              type="button"
              className="mt-2 text-[13px] font-bold underline-offset-2 hover:underline"
              onClick={() => setShowAll(!showAll)}
            >
              {showAll ? "Show fewer" : `Show all ${data.requirements.length}`}
            </button>
          ) : null}
        </div>
      ) : (
        <p className="text-[14px] text-grey-secondary">
          No requirements are counted for this project yet.
        </p>
      )}
    </Card>
  );
}

function completionText(data: RequirementsResponse): string {
  if (data.total === 0) return "No requirements counted yet";
  const base = data.has_points
    ? `${formatPoints(data.points_done)} of ${formatPoints(data.points_total)} points in production`
    : `${data.done} of ${data.total} in production`;
  return data.excluded > 0 ? `${base} · ${data.excluded} not counted` : base;
}

function moveText(from: DeliveryStage | null, to: DeliveryStage | null): string {
  if (from === null && to !== null) return `new, in ${STAGE_LABELS[to]}`;
  if (to === null && from !== null) return `left the scope from ${STAGE_LABELS[from]}`;
  if (from !== null && to !== null) return `${STAGE_LABELS[from]} → ${STAGE_LABELS[to]}`;
  return "";
}

function formatPoints(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

function dayLabel(iso: string): string {
  return new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
  });
}
