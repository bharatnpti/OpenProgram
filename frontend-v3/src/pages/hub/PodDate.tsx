import { useQuery } from "@tanstack/react-query";
import { useCallback } from "react";

import { apiClient } from "../../api/client";
import { useRole } from "../../app/role";
import { PanelState } from "../../components/PanelState";
import { PodTimeline } from "../../features/delivery/DeliveryTimeline";
import { TabIntro } from "../../features/hub/TabIntro";
import { usePodAccess } from "../../features/hub/useHub";
import { RequirementsSection } from "../../features/overall/RequirementsSection";
import type { Row } from "../../features/overall/timeline";
import { TimelineSection } from "../../features/overall/TimelineSection";
import { podReads } from "../../features/pods/podLevel";
import { PodSections, type PodSection } from "../../features/pods/PodSections";
import { WhatChanged } from "../../features/project/WhatChanged.tsx";
import { useReportAccess } from "../../features/reports/useReportAccess";
import { PodPart } from "../../features/today/TodayAnswer";
import { readState } from "../../lib/readState";

/**
 * Pod › Will we make the date? (`/projects/:projectId/pods/:podId/date`): the
 * pod's part of this project. Its answer with its three dates; its row on the
 * project's timeline, with Set or Change date where the server allows it, what
 * changed and why its date moved; the requirements below it by stage. The
 * dates are said once: the answer has them, so the pod's date strip is not
 * drawn again, and the row carries its date action. A developer reads no
 * project's timeline, so their own pod's timeline stands in, and no
 * requirements by stage.
 */
export function PodDate({ projectId, podId }: { projectId: string; podId: string }) {
  const { lens } = useRole();
  const { readPodDelivery } = useReportAccess();
  const { pod, full, ownDates } = usePodAccess(podId);
  const reads = podReads(lens, full);
  const readsDates = ownDates || readPodDelivery;
  const delivery = useQuery({
    queryKey: ["pod-delivery", podId],
    queryFn: () => apiClient.podDelivery(podId),
    enabled: readsDates && podId !== "",
  });
  const ownRow = useCallback(
    (row: Row) => row.scope_kind === "pod" && row.scope_id === podId,
    [podId],
  );
  if (!pod) return null;
  const part = delivery.data?.projects.find((item) => item.project_id === projectId);

  const sections: PodSection[] = [];
  if (readsDates) {
    sections.push({
      id: "answer",
      label: null,
      element: (
        <PanelState {...readState(delivery)} onRetry={() => void delivery.refetch()}>
          {part ? (
            <PodPart scope={part.pod} subject={pod.name} project={part.project_name} />
          ) : null}
        </PanelState>
      ),
    });
  }
  if (reads.projectTimeline) {
    sections.push({
      id: "timeline",
      label: "Timeline",
      ownsId: true,
      element: (
        <TimelineSection
          projectId={projectId}
          releaseId=""
          rows={ownRow}
          between={(picked) => <WhatChanged {...picked} />}
        />
      ),
    });
  } else if (ownDates) {
    sections.push({
      id: "timeline",
      label: "Timeline",
      element: <PodTimeline pod={pod} readable />,
    });
  }
  if (reads.requirements) {
    sections.push({
      id: "stages",
      label: "Stages",
      element: <RequirementsSection projectId={projectId} podId={podId} />,
    });
  }

  return (
    <div>
      <TabIntro
        tab="date"
        meta={`${pod.name}'s part of the project: ${
          reads.requirements
            ? "its dates, its row on the timeline and the requirements below it"
            : reads.projectTimeline
              ? "its dates and its row on the timeline"
              : "its dates and its timeline"
        }.`}
      />
      <PodSections sections={sections} />
    </div>
  );
}
