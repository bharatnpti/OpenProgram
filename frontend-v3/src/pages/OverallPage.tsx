import { useParams, useSearchParams } from "react-router-dom";

import { ApiError } from "../api/client";
import { ForecastSection } from "../features/overall/ForecastSection";
import { useGateBoard, useReleases } from "../features/overall/queries";
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
  // than a page of "No such release". A reader who cannot list releases (a
  // scrum master, a developer) learns it from the gate board, the read they
  // have: it answers 404 for a release that no longer exists. That query is the
  // gates section's own, so it costs no extra request.
  const list = releases.query.data;
  const probe = useGateBoard(projectId, releases.locked && asked ? asked : undefined);
  const goneForReader =
    releases.locked && probe.query.error instanceof ApiError && probe.query.error.status === 404;
  const gone =
    asked !== "" && ((list && !list.some((r) => r.release_id === asked)) || goneForReader);
  const releaseId = gone ? "" : asked;

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
