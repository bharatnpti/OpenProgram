import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import { useRole } from "../../app/role";
import { useDayWords } from "../../app/viewingDate";
import { PanelState } from "../../components/PanelState";
import { FactorsPanel } from "../../features/delivery/NodeBits";
import { TabIntro } from "../../features/hub/TabIntro";
import { usePodAccess } from "../../features/hub/useHub";
import { BlockersAndWaits } from "../../features/pods/BlockersAndWaits";
import { NudgeList } from "../../features/pods/NudgeList";
import { colourReasons } from "../../features/pods/podWords";
import { TodayStatus } from "../../features/pods/TodayStatus";
import { PodPeoplePanel } from "../../features/profiles/PodPeople";
import { WarningGroup } from "../../features/warnings/WarningGroups";
import { shownGroups } from "../../features/warnings/warningWords";

/**
 * Pod › Early warnings (`/projects/:projectId/pods/:podId/warnings`): the
 * pod's morning, for whoever reads its detail (its scrum master, a manager, an
 * admin, a developer in it): today's status of its tasks, who needs a nudge
 * (the readers of pod check-ins only), blockers and waits, why it has its
 * colour, and its people. Its morning is pod-wide, across every project it
 * works on. A reader of its dates only gets the project's warnings.
 */
export function PodWarnings({ projectId, podId }: { projectId: string; podId: string }) {
  const { access } = useRole();
  const day = useDayWords();
  const { pod, full } = usePodAccess(podId);
  const rollup = useQuery({
    queryKey: ["pod", podId, "rollup"],
    queryFn: () => apiClient.podRollup(podId),
    enabled: full,
  });
  const checkins = useQuery({
    queryKey: ["pod", podId, "checkins"],
    queryFn: () => apiClient.podCheckins(podId),
    enabled: full,
  });
  if (!pod) return null;
  if (!full) return <ProjectWarnings projectId={projectId} />;
  return (
    <div>
      <TabIntro tab="warnings" />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
        <section id="status" aria-label="Today's status" className="scroll-mt-28">
          <TodayStatus podId={pod.id} day={day} />
        </section>
        {checkins.data?.reads_ladder ? (
          <section id="checkins" aria-label="Who needs a nudge" className="scroll-mt-28">
            <NudgeList read={checkins} day={day} />
          </section>
        ) : null}
        <section id="blockers" aria-label="Blockers and waits" className="scroll-mt-28">
          <BlockersAndWaits
            podId={pod.id}
            memberIds={pod.member_ids}
            waits={access.coordination.board}
          />
        </section>
        {rollup.data ? (
          <FactorsPanel
            title="Why this colour"
            factors={colourReasons(rollup.data.factors)}
            names={rollup.data.source_names}
          />
        ) : null}
        <section id="people" aria-label="People" className="scroll-mt-28">
          <PodPeoplePanel podId={pod.id} />
        </section>
      </div>
    </div>
  );
}

/** The project's warnings, for a reader of the pod's dates only. */
function ProjectWarnings({ projectId }: { projectId: string }) {
  const { access } = useRole();
  const warnings = useQuery({
    queryKey: ["portfolio", "warnings", projectId],
    queryFn: () => apiClient.portfolioWarnings({ projectId }),
    enabled: access.hub.bulk,
  });
  const view = warnings.data;
  return (
    <div>
      <TabIntro tab="warnings" meta="The warnings on this project." />
      <PanelState isLoading={warnings.isLoading} error={warnings.error}>
        {view ? (
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2">
            {shownGroups(view).map((group) => (
              <WarningGroup key={group.kind} group={group} seenSince={view.seen_since} />
            ))}
          </div>
        ) : null}
      </PanelState>
    </div>
  );
}
