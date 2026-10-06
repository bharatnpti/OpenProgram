import { useParams, useSearchParams } from "react-router-dom";

import { ForecastSection } from "../features/overall/ForecastSection";
import { useReleases } from "../features/overall/queries";
import { ReleaseScope } from "../features/overall/ReleaseScope";
import { ReportsHeader } from "../features/reports/ReportsHeader";
import { GatesSection } from "../features/overall/GatesSection";
import { QuestionsSection } from "../features/overall/QuestionsSection";
import { RequirementsSection } from "../features/overall/RequirementsSection";
import { RisksSection } from "../features/overall/RisksSection";

/**
 * The project's state since it started, read today, for the whole project or
 * one release (`?release=`). Each section is its own query, so one refused or
 * slow read never blanks the others. Risks and escalation are always the whole
 * project's.
 */
export function OverallPage() {
  const { projectId = "" } = useParams();
  const [search, setSearch] = useSearchParams();
  const asked = search.get("release") ?? "";
  const releases = useReleases(projectId);
  // A link to a release that is gone falls back to the whole project rather
  // than a page of "No such release"; a reader who cannot list releases keeps
  // the link's scope, which the gates still honour.
  const list = releases.query.data;
  const releaseId = asked && list && !list.some((r) => r.release_id === asked) ? "" : asked;

  return (
    <>
      <ReportsHeader projectId={projectId} />
      <ReleaseScope
        projectId={projectId}
        releaseId={releaseId}
        onChange={(next) => setSearch(next ? { release: next } : {}, { replace: true })}
      />
      {asked && !releaseId ? (
        <p className="-mt-5 mb-8 text-[13px] text-grey-secondary">
          The release in the link no longer exists, so this shows the whole project.
        </p>
      ) : null}
      <div className="grid grid-cols-[minmax(0,1fr)] gap-12">
        <ForecastSection projectId={projectId} releaseId={releaseId} />
        <RequirementsSection projectId={projectId} releaseId={releaseId} />
        <GatesSection projectId={projectId} releaseId={releaseId} />
        <RisksSection projectId={projectId} />
        <QuestionsSection projectId={projectId} releaseId={releaseId} />
      </div>
    </>
  );
}
