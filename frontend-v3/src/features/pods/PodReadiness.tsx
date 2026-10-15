import { useRole } from "../../app/role";
import { useReadOnly } from "../../app/viewingDate";
import { PanelState, SectionHeader } from "../../components/PanelState";
import { ReadinessBoard } from "../overall/ReadinessSection";
import { hasPodReadiness, usePodReadiness } from "./podQueries";

/**
 * A pod's own release readiness (`GET /pods/{id}/readiness`): the criteria its
 * board under each of its projects lists, with the same rows, states and
 * actions as the project's board. Read as it stands now whatever day is shown
 * (the check keeps no history). The caller draws it only for its readers: a
 * manager, an admin, a scrum master for the pods they run. A tenant that has
 * not set readiness up gets no section.
 */
export function PodReadiness({
  projectId,
  podId,
  podName,
}: {
  projectId: string;
  podId: string;
  podName: string;
}) {
  const { access } = useRole();
  const { readOnly } = useReadOnly();
  const query = usePodReadiness(projectId, podId, true);
  const data = query.data;
  if (data && !hasPodReadiness(data)) return null;
  return (
    <section
      id="readiness"
      aria-labelledby="pod-readiness-h"
      className="scroll-mt-24 [contain-intrinsic-size:auto_420px] [content-visibility:auto]"
    >
      <SectionHeader
        id="pod-readiness-h"
        title="Pod readiness"
        meta={`What ${podName} needs before production beyond each requirement's gates. Checked against Jira every hour.`}
      />
      <PanelState
        isLoading={query.isLoading}
        error={query.error}
        onRetry={() => void query.refetch()}
        isEmpty={data !== undefined && data.findings.length === 0}
        emptyText="Nothing checked for this pod yet. The check runs every hour after the Jira sync."
      >
        {data ? (
          <ReadinessBoard
            data={data}
            projectId={projectId}
            answered={false}
            canAct={access.readiness.act && !readOnly}
          />
        ) : null}
      </PanelState>
    </section>
  );
}
