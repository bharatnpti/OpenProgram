import { Link } from "react-router-dom";

import { projectTabPath } from "../../app/links";
import { TabIntro } from "../../features/hub/TabIntro";
import { HygieneSection } from "../../features/overall/HygieneSection";
import { useReadsReadiness } from "../../features/overall/queries";

/**
 * Pod › Scope control (`/projects/:projectId/pods/:podId/scope`): Jira hygiene
 * for the project, whose own pod pick narrows it to a pod, and a way to the
 * project's plan and gates. The pod's own part of the plan, its gates and its
 * readiness come with the reads' pod filter.
 */
export function PodScope({ projectId, releaseId }: { projectId: string; releaseId: string }) {
  const hygiene = useReadsReadiness(projectId);
  return (
    <div>
      <TabIntro
        tab="scope"
        meta={
          <Link to={projectTabPath(projectId, "scope")} className="font-bold">
            The plan, the gates and readiness are on the project&apos;s Scope control →
          </Link>
        }
      />
      {hygiene ? <HygieneSection projectId={projectId} releaseId={releaseId} /> : null}
    </div>
  );
}
