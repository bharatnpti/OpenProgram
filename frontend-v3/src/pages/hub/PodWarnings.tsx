import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiClient } from "../../api/client";
import { projectTabPath } from "../../app/links";
import { useRole } from "../../app/role";
import { useDayWords } from "../../app/viewingDate";
import { PanelState, SectionHeader } from "../../components/PanelState";
import { FactorsPanel } from "../../features/delivery/NodeBits";
import { TabIntro } from "../../features/hub/TabIntro";
import { usePodAccess } from "../../features/hub/useHub";
import { BlockersAndWaits } from "../../features/pods/BlockersAndWaits";
import { NudgeList } from "../../features/pods/NudgeList";
import { podReads } from "../../features/pods/podLevel";
import { PodSections, type PodSection } from "../../features/pods/PodSections";
import { colourReasons } from "../../features/pods/podWords";
import { TodayStatus } from "../../features/pods/TodayStatus";
import { PrFlowSection } from "../../features/signals/PrFlowSection";
import { useWarnings } from "../../features/warnings/queries";
import { WarningGroup } from "../../features/warnings/WarningGroups";
import { blockersAboveWords, countLine, shownGroups } from "../../features/warnings/warningWords";
import { formatDay } from "../../lib/format";
import { readState } from "../../lib/readState";

/**
 * Pod › Early warnings (`/projects/:projectId/pods/:podId/warnings`): the pod's
 * morning, then its warnings. For whoever reads its detail (its scrum master, a
 * manager, an admin, a developer in it): its open blockers and the waits among
 * its people, who needs a nudge (the readers of pod check-ins only), today's
 * status of its tasks with what Jira says and when each is due. Then, for every
 * reader of its warnings, what Jira and Git raise on its part of this project,
 * in counts and teams where the server words them so. Then why it has its
 * colour and its review flow. Its morning is pod-wide, across every project it
 * works on; its warnings are this project's part. Blockers are said once: in
 * Blockers and waits where it is drawn, else as the warnings' first group.
 */
export function PodWarnings({ projectId, podId }: { projectId: string; podId: string }) {
  const { lens, access } = useRole();
  const day = useDayWords();
  const { pod, full } = usePodAccess(podId);
  const reads = podReads(lens, full);
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
  const warnings = useWarnings({ projectId, podId }, reads.warnings);
  if (!pod) return null;
  const view = warnings.data;
  const groups = view
    ? shownGroups(view).filter((group) => !(full && group.kind === "blockers"))
    : [];
  // Said once: with its morning drawn, the blockers are the ones listed above.
  const above = full ? (view?.groups.find((group) => group.kind === "blockers")?.count ?? 0) : 0;

  const sections: PodSection[] = [];
  if (full) {
    sections.push({
      id: "blockers",
      label: "Blockers",
      element: (
        <BlockersAndWaits
          podId={pod.id}
          memberIds={pod.member_ids}
          waits={access.coordination.board}
        />
      ),
    });
    if (checkins.data?.reads_ladder) {
      sections.push({
        id: "checkins",
        label: "Nudges",
        element: <NudgeList read={checkins} day={day} />,
      });
    }
    sections.push({
      id: "status",
      label: "Status",
      element: <TodayStatus podId={pod.id} day={day} />,
    });
  }
  if (reads.warnings) {
    sections.push({
      id: "warnings",
      label: "Warnings",
      element: (
        <section aria-labelledby="pod-warnings-h">
          <SectionHeader
            id="pod-warnings-h"
            title={`Warnings for ${pod.name}`}
            meta={
              view
                ? `What Jira and Git say on its part of the project, whatever anyone reports. ${countLine(view, true)}${
                    above > 0 ? ` ${blockersAboveWords(above)}` : ""
                  }${
                    view.seen_since
                      ? ` Ages of disagreements count from ${formatDay(view.seen_since)}, when they were first recorded.`
                      : ""
                  }`
                : undefined
            }
          />
          <PanelState {...readState(warnings)} onRetry={() => void warnings.refetch()}>
            {view ? (
              <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2">
                {groups.map((group) => (
                  <WarningGroup
                    key={group.kind}
                    group={group}
                    seenSince={view.seen_since}
                    scope="pod"
                  />
                ))}
              </div>
            ) : null}
          </PanelState>
          <p className="mt-3 text-[13px]">
            <Link
              to={projectTabPath(projectId, "warnings")}
              className="inline-flex min-h-11 items-center font-bold sm:min-h-0"
            >
              The whole project&apos;s warnings →
            </Link>
          </p>
        </section>
      ),
    });
  }
  if (full && rollup.data) {
    sections.push({
      id: "why",
      label: "Why this colour",
      element: (
        <FactorsPanel
          title="Why this colour"
          factors={colourReasons(rollup.data.factors)}
          names={rollup.data.source_names}
        />
      ),
    });
  }
  if (reads.flow) {
    sections.push({
      id: "flow",
      label: "Review flow",
      element: (
        <>
          <PrFlowSection full={false} enabled defaultScope={`pod:${pod.id}`} />
          <p className="mt-3 text-[13px]">
            <Link
              to={`/flow?scope=${encodeURIComponent(`pod:${pod.id}`)}`}
              className="inline-flex min-h-11 items-center font-bold sm:min-h-0"
            >
              Review flow for {pod.name} →
            </Link>
          </p>
        </>
      ),
    });
  }

  return (
    <div>
      <TabIntro
        tab="warnings"
        meta={
          full
            ? `${pod.name}'s morning across every project it works on, then the warnings on its part of this one.`
            : `The warnings on ${pod.name}'s part of the project.`
        }
      />
      <PodSections sections={sections} />
    </div>
  );
}
