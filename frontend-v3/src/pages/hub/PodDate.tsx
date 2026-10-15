import { PodDateStrips } from "../../features/delivery/DeliveryStrips";
import { PodTimeline } from "../../features/delivery/DeliveryTimeline";
import { TabIntro } from "../../features/hub/TabIntro";
import { usePodAccess } from "../../features/hub/useHub";
import { useReportAccess } from "../../features/reports/useReportAccess";

/**
 * Pod › Will we make the date? (`/projects/:projectId/pods/:podId/date`): the
 * pod's dates (Set or Change date) and its own timeline row: done, the likely
 * finish and what changed. Every pod that opens shows its dates: that is all
 * a dates-only reader has.
 */
export function PodDate({ projectId, podId }: { projectId: string; podId: string }) {
  const { pod, ownDates } = usePodAccess(podId);
  const { readPodDelivery } = useReportAccess();
  if (!pod) return null;
  return (
    <div>
      <TabIntro tab="date" />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-3">
        <PodDateStrips pod={pod} projectId={projectId} readable={ownDates ? true : undefined} />
        <PodTimeline pod={pod} readable={ownDates ? true : readPodDelivery} />
      </div>
    </div>
  );
}
