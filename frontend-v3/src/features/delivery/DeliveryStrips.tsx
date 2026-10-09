import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { apiClient } from "../../api/client";
import type { DirectoryItemResponse, ScopeDeliveryResponse } from "../../api/schema";
import { PanelState } from "../../components/PanelState";
import { CompactDateStrip, DateStrip } from "../../components/ui/DateStrip";
import { Pill } from "../../components/ui/Pill";
import { formatDate } from "../../lib/format";
import { podLaterThanProject } from "../overall/overallWords";
import { useDelivery } from "../overall/queries";
import { DeliveryDateDialog } from "../reports/DeliveryDateDialog";
import { Locked } from "../reports/ReportDialog";
import { useReportAccess } from "../reports/useReportAccess";
import { DateHistory } from "./DateHistory";

/*
 * The DateStrip wherever a project or pod is shown, each reading its own
 * delivery: `/projects/{id}/delivery` (product owner, manager, executive, admin)
 * under the key Overall uses, and `/pods/{id}/delivery` (also a scrum master for
 * their own pods). A role that reads neither is never offered a strip.
 */

type Editing = { scope: ScopeDeliveryResponse; title: string } | null;

/** A project's own read, shared with Overall's forecast (the same query). */
const useProjectDelivery = (projectId: string) => useDelivery(projectId).query;

/**
 * The project's strip, with Change date (or Set date) for whoever commits it:
 * a product owner, manager or admin. On a past day the button is off, with why.
 */
export function ProjectDateStrip({ projectId, title }: { projectId: string; title?: string }) {
  const access = useReportAccess();
  const delivery = useProjectDelivery(projectId);
  const [editing, setEditing] = useState<Editing>(null);
  if (!access.readProjectProgress) return null;
  const scope = delivery.data?.project;
  const offNow = access.pastDay("setProjectDates");

  return (
    <>
      <PanelState
        isLoading={delivery.isLoading}
        error={delivery.error}
        onRetry={() => void delivery.refetch()}
      >
        {scope ? (
          <DateStrip
            scope={scope}
            title={title}
            action={
              access.setProjectDates ? (
                <Pill
                  size="sm"
                  variant="ghost"
                  onClick={() => setEditing({ scope, title: `${scope.name}: delivery date` })}
                >
                  {scope.commitment.target_date ? "Change date" : "Set date"}
                </Pill>
              ) : offNow ? (
                <Locked>{offNow}</Locked>
              ) : null
            }
          />
        ) : null}
      </PanelState>
      {editing ? (
        <DeliveryDateDialog
          scope={editing.scope}
          title={editing.title}
          onClose={() => setEditing(null)}
        />
      ) : null}
    </>
  );
}

/** The project's strip on one line, for a list of projects; nothing until it is read. */
export function ProjectCompactStrip({
  projectId,
  className = "mt-1",
}: {
  projectId: string;
  className?: string;
}) {
  const delivery = useProjectDelivery(projectId);
  if (delivery.isLoading) {
    return <p className={`${className} text-[13px] text-grey-secondary`}>Loading the dates…</p>;
  }
  const scope = delivery.data?.project;
  return scope ? <CompactDateStrip scope={scope} className={className} /> : null;
}

/**
 * A pod's strip for each project it works on: the pod's own date, with the
 * project's date in the caption, and Set date for the pod's scrum master (a
 * manager or admin may too). The server says per pod whether this reader may
 * (`can_set_dates`); on a past day nobody may, and the strip says why.
 */
export function PodDateStrips({
  pod,
  projectId,
}: {
  pod: DirectoryItemResponse;
  /** Only the pod's part of this project (Overall), titled by the pod alone. */
  projectId?: string;
}) {
  const { readPodDelivery, readOnly, reason } = useReportAccess();
  const delivery = useQuery({
    queryKey: ["pod-delivery", pod.id],
    queryFn: () => apiClient.podDelivery(pod.id),
    enabled: readPodDelivery,
  });
  const [editing, setEditing] = useState<Editing>(null);
  if (!readPodDelivery) return null;
  const data = delivery.data;
  const serverSays = data?.can_set_dates ?? false;
  const canSet = serverSays && !readOnly;
  const projects = (data?.projects ?? []).filter(
    (item) => !projectId || item.project_id === projectId,
  );

  return (
    <>
      <PanelState
        isLoading={delivery.isLoading}
        error={delivery.error}
        onRetry={() => void delivery.refetch()}
        isEmpty={projects.length === 0}
        emptyText="This pod works on no project yet, so it has no date to commit."
      >
        <div className="grid grid-cols-[minmax(0,1fr)] gap-3">
          {projects.map((item) => {
            const target = item.pod.commitment.target_date;
            return (
              <DateStrip
                key={item.project_id}
                scope={item.pod}
                title={projectId ? pod.name : `${pod.name} for ${item.project_name}`}
                action={
                  canSet ? (
                    <Pill
                      size="sm"
                      variant="ghost"
                      aria-label={`${target ? "Change" : "Set"} ${pod.name}'s date for ${item.project_name}`}
                      onClick={() =>
                        setEditing({
                          scope: item.pod,
                          title: `${pod.name}'s date for ${item.project_name}`,
                        })
                      }
                    >
                      {target ? "Change date" : "Set date"}
                    </Pill>
                  ) : readOnly && serverSays && reason ? (
                    <Locked>{reason}</Locked>
                  ) : null
                }
                caption={
                  <>
                    <span>
                      {item.project_name}:{" "}
                      {item.project_target ? (
                        formatDate(item.project_target)
                      ) : (
                        <span className="font-bold text-rag-red">no committed date</span>
                      )}
                      {item.pod.total > 0
                        ? ` · ${item.pod.open} of ${item.pod.total} requirements open`
                        : ""}
                    </span>
                    {podLaterThanProject(target, item.project_target) ? (
                      <span className="ml-1 font-bold text-rag-amber">
                        Committed after the project&apos;s date.
                      </span>
                    ) : null}
                  </>
                }
              >
                <DateHistory changes={item.pod.commitment.changes} />
              </DateStrip>
            );
          })}
        </div>
      </PanelState>
      {editing ? (
        <DeliveryDateDialog
          scope={editing.scope}
          title={editing.title}
          onClose={() => setEditing(null)}
        />
      ) : null}
    </>
  );
}
