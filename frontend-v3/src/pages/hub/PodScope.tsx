import { Link, useParams } from "react-router-dom";

import type { ScopeProjectDto } from "../../api/schema";
import { projectTabPath } from "../../app/links";
import { useRole } from "../../app/role";
import { PanelState, SectionHeader } from "../../components/PanelState";
import { TabIntro } from "../../features/hub/TabIntro";
import { usePodAccess } from "../../features/hub/useHub";
import { GatesSection } from "../../features/overall/GatesSection";
import { HygieneSection } from "../../features/overall/HygieneSection";
import { useReadsReadiness } from "../../features/overall/queries";
import { VizCard } from "../../features/overall/viz";
import { podPlanLine, podReads, podScopeLine } from "../../features/pods/podLevel";
import { hasPodReadiness, usePodReadiness } from "../../features/pods/podQueries";
import { PodReadiness } from "../../features/pods/PodReadiness";
import { PodSections, type PodSection } from "../../features/pods/PodSections";
import { useScopeRead } from "../../features/warnings/queries";
import { formatDay } from "../../lib/format";
import { readState } from "../../lib/readState";

/**
 * Pod › Scope control (`/projects/:projectId/pods/:podId/scope`): the pod's
 * part of the plan (its requirements that were planned, joined after, or
 * left), the acceptance gates of its requirements, its own readiness board,
 * and the Jira hygiene of its issues. Marking planning complete and a new
 * baseline stay with the whole project, one link away. Each section is drawn
 * only for its readers; the server still decides, and an executive reads
 * counts.
 */
export function PodScope({ projectId }: { projectId: string; releaseId?: string }) {
  const { podId = "" } = useParams();
  const { lens } = useRole();
  const { pod, full } = usePodAccess(podId);
  const reads = podReads(lens, full);
  const hygiene = useReadsReadiness(projectId);
  const scope = useScopeRead({ projectId, podId }, reads.scope && podId !== "");
  // Drawn, and in the index, only where the tenant set readiness up or it found something.
  const readiness = usePodReadiness(projectId, podId, reads.readiness);
  if (!pod) return null;
  const row = scope.data?.projects.find((project) => project.id === projectId);

  const sections: PodSection[] = [];
  if (reads.scope && scope.data?.reads_baselines !== false) {
    sections.push({
      id: "baseline",
      label: "Against the plan",
      element: (
        <section aria-labelledby="pod-plan-h">
          <SectionHeader
            id="pod-plan-h"
            title="Against the plan"
            meta={`${pod.name}'s requirements against the project's planning baseline`}
          />
          <PanelState {...readState(scope)} onRetry={() => void scope.refetch()}>
            {scope.data ? <PodPlan row={row} podName={pod.name} projectId={projectId} /> : null}
          </PanelState>
        </section>
      ),
    });
  }
  sections.push({
    id: "gates",
    label: "Gates",
    element: <GatesSection projectId={projectId} podId={podId} />,
  });
  if (reads.readiness && hasPodReadiness(readiness.data)) {
    sections.push({
      id: "readiness",
      label: "Readiness",
      ownsId: true,
      element: <PodReadiness projectId={projectId} podId={podId} podName={pod.name} />,
    });
  }
  if (hygiene) {
    sections.push({
      id: "hygiene",
      label: "Jira hygiene",
      ownsId: true,
      element: <HygieneSection projectId={projectId} podId={podId} />,
    });
  }

  return (
    <div>
      <TabIntro
        tab="scope"
        meta={podScopeLine(
          pod.name,
          sections.map((section) => section.id),
        )}
      />
      <PodSections sections={sections} />
    </div>
  );
}

function PodPlan({
  row,
  podName,
  projectId,
}: {
  row: ScopeProjectDto | undefined;
  podName: string;
  projectId: string;
}) {
  const plan = row?.plan;
  return (
    <VizCard className="gap-3">
      {plan ? (
        <>
          <h3 className="text-[17px] font-extrabold leading-snug text-balance">
            {plan.added === 0
              ? `None of ${podName}'s requirements joined after planning.`
              : `${plan.added} of ${podName}'s requirements joined after planning.`}
          </h3>
          <p className="text-[14px] text-grey-body">{podPlanLine(plan)}</p>
          {row && row.added_keys.length > 0 ? (
            <p className="text-[13px] text-grey-body break-words">
              Added after planning:{" "}
              {row.added_keys.map((key, index) => (
                <span key={key}>
                  {index > 0 ? ", " : ""}
                  <span className="font-bold text-ink">{key}</span>
                  {row.without_label_keys.includes(key) ? (
                    <span className="text-grey-secondary"> (no {plan.change_label} label)</span>
                  ) : null}
                </span>
              ))}
              .
            </p>
          ) : null}
          {row?.scope_day ? (
            <p className="text-[12px] text-grey-secondary">
              Read from the scope kept on {formatDay(row.scope_day)}.
            </p>
          ) : null}
        </>
      ) : (
        <p className="text-[14px] text-grey-body">
          The project has no planning baseline yet, so nothing is measured against a plan.
        </p>
      )}
      <p className="text-[13px]">
        <Link
          to={projectTabPath(projectId, "scope", "baseline")}
          className="inline-flex min-h-11 items-center font-bold sm:min-h-0"
        >
          The project&apos;s planning baseline →
        </Link>
      </p>
    </VizCard>
  );
}
