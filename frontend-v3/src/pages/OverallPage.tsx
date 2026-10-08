import { useParams, useSearchParams } from "react-router-dom";

import { ApiError } from "../api/client";
import { useRole } from "../app/role";
import { useViewingDate } from "../app/viewingDate";
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
 * slow read never blanks the others. Risks are always the whole project's.
 * Each role gets the sections it reads (app/access.ts): a developer only the
 * gates (they sign test cases off) and the questions, a scrum master also the
 * dates of the project's pods and its risks, the project's readers all of it.
 */
export function OverallPage() {
  const { projectId = "" } = useParams();
  const [search, setSearch] = useSearchParams();
  const { asOf, label } = useViewingDate();
  const { overall } = useRole().access;
  const asked = search.get("release") ?? "";
  const releases = useReleases(projectId);
  // A link to a release that is gone falls back to the whole project rather
  // than a page of "No such release". A reader who cannot list releases (a
  // scrum master, a developer) learns it from the gate board, the read they
  // have: it answers 404 for a release that no longer exists. That query is the
  // gates section's own, so it costs no extra request.
  const list = releases.query.data;
  const probe = useGateBoard(projectId, !releases.readable && asked ? asked : undefined);
  const goneForReader =
    !releases.readable && probe.query.error instanceof ApiError && probe.query.error.status === 404;
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
        <p className={`${releases.readable ? "-mt-5" : ""} mb-8 text-[13px] text-grey-secondary`}>
          The release in the link no longer exists, so this shows the whole project.
        </p>
      ) : null}
      {asOf ? (
        <p className="mb-8 rounded-2xl bg-grey-fill px-4 py-3 text-[13px] text-grey-body">
          {pastDayWords(overall, label ?? "")} Gate sign-offs and questions are not kept per day:
          they show how they stand now, for the requirements of that day.
        </p>
      ) : null}
      <div className="grid grid-cols-[minmax(0,1fr)] gap-12">
        <ForecastSection projectId={projectId} releaseId={releaseId} />
        {overall.requirements ? (
          <RequirementsSection projectId={projectId} releaseId={releaseId} />
        ) : null}
        <GatesSection projectId={projectId} releaseId={releaseId} />
        {overall.risks ? <RisksSection projectId={projectId} /> : null}
        <QuestionsSection projectId={projectId} releaseId={releaseId} />
      </div>
    </>
  );
}

/** What a past day shows as it stood, named by the sections this role has. */
function pastDayWords(
  overall: { forecast: boolean; podDates: boolean; requirements: boolean; risks: boolean },
  label: string,
): string {
  const kept = [
    overall.forecast ? "the forecast" : overall.podDates ? "the pods' dates" : null,
    overall.requirements ? "requirements" : null,
    overall.risks ? "risks" : null,
  ].filter((part): part is string => part !== null);
  if (kept.length === 0) return "";
  const joined =
    kept.length === 1 ? kept[0] : `${kept.slice(0, -1).join(", ")} and ${kept[kept.length - 1]}`;
  const one = kept.length === 1 && kept[0] === "the forecast";
  return `${joined.charAt(0).toUpperCase()}${joined.slice(1)} ${one ? "is as it" : "are as they"} stood on ${label}.`;
}
