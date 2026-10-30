import { Link } from "react-router-dom";

import { dailyPath } from "../../app/links";
import { useRole } from "../../app/role";
import { TabIntro } from "../../features/hub/TabIntro";
import { Briefs } from "../../features/reports/Briefs";

/**
 * Pod › Reports (`/projects/:projectId/pods/:podId/reports`): the pod's daily
 * briefs, and the way to the project's day report, which covers the whole
 * project.
 */
export function PodReports({ projectId, podId }: { projectId: string; podId: string }) {
  const { access } = useRole();
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-6">
      <TabIntro
        tab="reports"
        meta={
          access.reports.daily ? (
            <Link to={dailyPath(projectId)} className="font-bold">
              The day report covers the whole project →
            </Link>
          ) : (
            "This pod's daily briefs, written from the day's facts."
          )
        }
      />
      {access.coordination.briefs ? (
        <Briefs
          kind="daily_pod"
          scopeId={podId}
          title="This pod's daily briefs"
          empty="No daily brief for this pod yet. They are written from the day's facts on a schedule."
        />
      ) : null}
    </div>
  );
}
