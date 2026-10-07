import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { apiClient } from "../../api/client";
import type { DirectoryItemResponse, PodDeliveryResponse } from "../../api/schema";
import { useRole } from "../../app/role";
import { useShownDay } from "../../app/viewingDate";
import { PanelState } from "../../components/PanelState";
import { Panel } from "../../components/ui/Bits";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { formatDate, formatDay } from "../../lib/format";
import { VERDICT_LABELS, toneForVerdict } from "../../lib/status";
import {
  inScope,
  isPastDay,
  podLaterThanProject,
  undatedWords,
  verdictCause,
} from "../overall/overallWords";
import { WHO } from "../reports/access";
import { DeliveryDateDialog } from "../reports/DeliveryDateDialog";
import { Locked } from "../reports/ReportDialog";
import { useReportAccess } from "../reports/useReportAccess";

type PodProjectDeliveryResponse = PodDeliveryResponse["projects"][number];

/**
 * The date this pod commits for its part of each project it works on, beside
 * the project's own date, with the forecast for the pod's requirements. The
 * pod's scrum master sets it; a manager or admin may too. The server says per
 * pod whether this reader may (`can_set_dates`); on a past day nobody may.
 */
export function PodDeliveryCard({ pod }: { pod: DirectoryItemResponse }) {
  const { roleLabel } = useRole();
  const { readPodDelivery, readOnly, reason } = useReportAccess();
  const delivery = useQuery({
    queryKey: ["pod-delivery", pod.id],
    queryFn: () => apiClient.podDelivery(pod.id),
    enabled: readPodDelivery,
  });
  const [editing, setEditing] = useState<PodProjectDeliveryResponse | null>(null);

  if (!readPodDelivery) {
    return (
      <Locked className="mb-5">
        Delivery dates open for {WHO.podDelivery}. You are viewing as {roleLabel.toLowerCase()}.
      </Locked>
    );
  }
  const data = delivery.data;
  const serverSays = data?.can_set_dates ?? false;
  const canSet = serverSays && !readOnly;

  return (
    <Panel
      className="mb-5"
      title="Delivery dates"
      note={canSet ? "Yours to set for this pod" : undefined}
    >
      <PanelState
        needs={WHO.podDelivery}
        isLoading={delivery.isLoading}
        error={delivery.error}
        onRetry={() => void delivery.refetch()}
        isEmpty={(data?.projects ?? []).length === 0}
        emptyText="This pod works on no project yet, so it has no date to commit."
      >
        <ul className="grid grid-cols-[minmax(0,1fr)] gap-3">
          {(data?.projects ?? []).map((item) => (
            <PodProjectRow
              key={item.project_id}
              item={item}
              canSet={canSet}
              onEdit={() => setEditing(item)}
            />
          ))}
        </ul>
        {!canSet ? (
          <Locked className="mt-3">
            {readOnly && serverSays && reason ? reason : `A pod's date is set by ${WHO.podDates}.`}
          </Locked>
        ) : null}
      </PanelState>
      {editing ? (
        <DeliveryDateDialog
          scope={editing.pod}
          title={`${pod.name}'s date for ${editing.project_name}`}
          onClose={() => setEditing(null)}
        />
      ) : null}
    </Panel>
  );
}

/**
 * One project the pod works on: its verdict, both dates, and the forecast
 * behind it. Titled by the project on the pod's panel, by the pod on Overall.
 */
export function PodProjectRow({
  item,
  title = item.project_name,
  canSet,
  onEdit,
}: {
  item: PodProjectDeliveryResponse;
  title?: string;
  canSet: boolean;
  onEdit: () => void;
}) {
  const scope = item.pod;
  const shownDay = useShownDay();
  const counted = inScope(scope);
  const target = scope.commitment.target_date;
  const history = scope.history;
  // Why the verdict is what it is, beside the verdict: the pod's undated
  // requirements are the only thing that makes it "At risk" on a short history.
  const cause = counted ? verdictCause(scope) : null;
  const undatedSaid = Boolean(cause?.because.includes("no ETA or due date"));

  return (
    <li className="rounded-2xl border border-grey-border p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[15px] font-extrabold">{title}</span>
            {counted ? (
              <RagChip tone={toneForVerdict(scope.verdict)} className="h-6 px-2.5 text-[12px]">
                {VERDICT_LABELS[scope.verdict]}
              </RagChip>
            ) : (
              <RagChip tone="neutral" className="h-6 px-2.5 text-[12px]">
                Nothing in scope
              </RagChip>
            )}
          </div>
          <p className="mt-1 text-[13px] text-grey-body">
            Pod: <strong className="text-ink">{target ? formatDate(target) : "no date"}</strong>
            {" · "}Project: {item.project_target ? formatDate(item.project_target) : "no date"}
            {counted ? ` · ${scope.open} of ${scope.total} requirements open` : ""}
          </p>
          {cause ? (
            <p
              className={`mt-1 text-[13px] font-bold ${scope.verdict === "off_track" ? "text-rag-red" : "text-rag-amber"}`}
            >
              {cause.because}
            </p>
          ) : null}
          {podLaterThanProject(target, item.project_target) ? (
            <p className="mt-1 text-[13px] font-bold text-rag-amber">
              Committed after the project&apos;s date.
            </p>
          ) : null}
        </div>
        {canSet ? (
          <Pill
            size="sm"
            variant="ghost"
            aria-label={`${target ? "Change" : "Set"} ${scope.name}'s date for ${item.project_name}`}
            onClick={onEdit}
          >
            {target ? "Change date" : "Set date"}
          </Pill>
        ) : null}
      </div>
      <p className="mt-2 text-[12px] text-grey-secondary">
        {!counted
          ? "No requirements are counted for this pod yet: the forecast starts once its requirements are."
          : history.p50
            ? `Completion rate says ${formatDay(history.p50)} (50% likely)${history.p85 ? `, ${formatDay(history.p85)} (85% likely)` : ""}.`
            : (history.reason ?? "Not enough history to forecast yet.")}
        {counted && scope.team.latest
          ? ` The team's latest date is ${formatDay(scope.team.latest)}${scope.team.latest_key ? ` (${scope.team.latest_key})` : ""}${isPastDay(scope.team.latest, shownDay) ? ", past its date" : ""}.`
          : ""}
        {counted && scope.team.undated > 0 && !undatedSaid
          ? ` ${undatedWords(scope.team.undated)}.`
          : ""}
      </p>
    </li>
  );
}
