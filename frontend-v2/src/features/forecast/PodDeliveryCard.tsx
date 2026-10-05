import { useQuery } from "@tanstack/react-query";
import { CalendarClock } from "lucide-react";
import { useState } from "react";

import { apiClient } from "../../api/client";
import type { ScopeDeliveryResponse } from "../../api/schema";
import { Card } from "../../components/ui/Card";
import { useViewingDate } from "../../app/viewingDate";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { errorMessage } from "../admin/adminTypes";
import { DateDialog } from "./DeliveryForecastCard";
import { VERDICT_LABELS, VERDICT_TONES, dayLabel, historyLabel, teamLabel } from "./forecast";

/**
 * The pod's part of each project it works on: the date its scrum master
 * commits, the project's own date beside it, and the forecast for the pod's
 * requirements.
 */
export function PodDeliveryCard({ podId, asOf }: { podId: string; asOf: string }) {
  const delivery = useQuery({
    queryKey: ["persona", "pod-delivery", podId, asOf],
    queryFn: () => apiClient.podDelivery(podId, asOf),
  });
  const [editing, setEditing] = useState<ScopeDeliveryResponse | null>(null);
  const { isPast } = useViewingDate();

  if (delivery.isError) {
    return (
      <Card padding="p-6">
        <h3 className="text-[18px] font-bold">Delivery dates</h3>
        <p className="mt-2 text-[14px] text-rag-red">{errorMessage(delivery.error)}</p>
      </Card>
    );
  }
  if (!delivery.data) return null;
  const { projects } = delivery.data;
  const canSet = delivery.data.can_set_dates && !isPast;
  if (projects.length === 0) return null;

  return (
    <Card padding="p-6" className="flex flex-col gap-4">
      <div>
        <h3 className="text-[18px] font-bold">Delivery dates</h3>
        <p className="mt-0.5 text-[13px] text-grey-secondary">
          The date this pod commits for its part of each project
          {canSet ? "; yours to set as its scrum master" : ""}.
        </p>
      </div>
      {projects.map((item) => {
        const scope = item.pod;
        const later =
          scope.commitment.target_date &&
          item.project_target &&
          scope.commitment.target_date > item.project_target;
        return (
          <div key={item.project_id} className="rounded-2xl border border-grey-border p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-[15px] font-bold">{item.project_name}</span>
                  <RagChip tone={VERDICT_TONES[scope.verdict]} className="h-6 text-[12px]">
                    {VERDICT_LABELS[scope.verdict]}
                  </RagChip>
                </div>
                <p className="mt-1 text-[13px] text-grey-secondary">
                  Pod:{" "}
                  <strong className="text-ink">{dayLabel(scope.commitment.target_date)}</strong>
                  {" · "}Project: {dayLabel(item.project_target)}
                  {" · "}
                  {scope.open} of {scope.total} open
                </p>
                {later ? (
                  <p className="mt-1 text-[13px] text-rag-amber">
                    Committed after the project&apos;s date.
                  </p>
                ) : null}
              </div>
              {canSet ? (
                <Pill variant="ghost" size="sm" onClick={() => setEditing(scope)}>
                  <CalendarClock size={14} />
                  {scope.commitment.target_date ? "Change date" : "Set date"}
                </Pill>
              ) : null}
            </div>
            <p className="mt-2 text-[13px]">
              <span className="text-grey-secondary">History says </span>
              {historyLabel(scope)}
              <span className="text-grey-secondary"> · team dates </span>
              {teamLabel(scope)}
            </p>
          </div>
        );
      })}
      {editing ? (
        <DateDialog
          title={`${editing.name}: ${projects.find((p) => p.project_id === editing.project_id)?.project_name ?? ""}`}
          scope={editing}
          asOf={asOf}
          onClose={() => setEditing(null)}
        />
      ) : null}
    </Card>
  );
}
