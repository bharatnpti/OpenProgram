import { Fragment } from "react";
import { useSearchParams } from "react-router-dom";

import { ApiError } from "../api/client";
import { useRole } from "../app/role";
import { useViewingDate } from "../app/viewingDate";
import { GateAnswer, ProjectAnswer, ScrumMasterAnswer } from "../features/overall/AnswerCard";
import { BaselineSection } from "../features/overall/BaselineSection";
import { GatesSection } from "../features/overall/GatesSection";
import { HygieneSection } from "../features/overall/HygieneSection";
import { useGateBoard, useReadsReadiness, useReleases } from "../features/overall/queries";
import { QuestionsSection } from "../features/overall/QuestionsSection";
import { ReadinessSection } from "../features/overall/ReadinessSection";
import { ReleaseScope } from "../features/overall/ReleaseScope";
import { RequirementsSection } from "../features/overall/RequirementsSection";
import { RisksSection } from "../features/overall/RisksSection";
import { overallSections } from "../features/overall/sections";
import { TimelineSection } from "../features/overall/TimelineSection";

/**
 * The project's state since it started, read today, for the whole project or
 * one release (`?release=`), from summary to detail, in the order
 * features/overall/sections.ts keeps: the answer line with its date strip, the
 * Timeline (each row from planning to its committed date, then why the picked
 * row's date moved), the planning baseline, requirements by stage (O3), the
 * gates (O5), release readiness, Jira hygiene, risks and questions. Date
 * first, then scope, then quality, then people. Each section is its own query,
 * so one refused or slow read never blanks the others. Risks are always the
 * whole project's.
 *
 * Each role gets the sections it reads (app/access.ts):
 * - the project's readers (product owner, manager, executive, admin): all of it,
 *   with Mark planning complete for the product owner, manager and admin; an
 *   executive reads Jira hygiene's counts, never who;
 * - a scrum master: an answer line for the pod they run, the Timeline's pods
 *   (theirs whole with Set date, the others' dates), the gates, release
 *   readiness and Jira hygiene where one of their pods works on the project,
 *   risks and questions;
 * - a developer: an answer line on the gates, the gates and the questions.
 */
export function OverallBody({ projectId }: { projectId: string }) {
  const [search, setSearch] = useSearchParams();
  const { asOf, label } = useViewingDate();
  const { overall } = useRole().access;
  // A scrum master reads it only for a project one of their pods works on.
  const readiness = useReadsReadiness(projectId);
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
  const scrumMaster = !overall.forecast && overall.timeline;
  const developer = !overall.forecast && !overall.timeline;

  return (
    <>
      <div className="op-viz">
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
            {readiness ? " Release readiness shows how it stands now." : ""}
          </p>
        ) : null}
        <div className="grid grid-cols-[minmax(0,1fr)] gap-10 max-sm:gap-8">
          {overallSections({
            answer: overall.forecast ? (
              <ProjectAnswer projectId={projectId} releaseId={releaseId} />
            ) : scrumMaster ? (
              <ScrumMasterAnswer projectId={projectId} />
            ) : (
              <GateAnswer projectId={projectId} releaseId={releaseId} />
            ),
            timeline: overall.timeline ? (
              <TimelineSection projectId={projectId} releaseId={releaseId} />
            ) : null,
            baseline: overall.forecast ? (
              <BaselineSection projectId={projectId} releaseId={releaseId} />
            ) : null,
            requirements: overall.requirements ? (
              <RequirementsSection projectId={projectId} releaseId={releaseId} />
            ) : null,
            gates: (
              <GatesSection projectId={projectId} releaseId={releaseId} summary={!developer} />
            ),
            readiness: readiness ? (
              <ReadinessSection
                projectId={projectId}
                releaseId={releaseId}
                answered={overall.forecast}
              />
            ) : null,
            hygiene: <HygieneSection projectId={projectId} releaseId={releaseId} />,
            risks: overall.risks ? <RisksSection projectId={projectId} /> : null,
            questions: <QuestionsSection projectId={projectId} releaseId={releaseId} />,
          }).map(({ key, element }) => (
            <Fragment key={key}>{element}</Fragment>
          ))}
        </div>
      </div>
    </>
  );
}

/** What a past day shows as it stood, named by the sections this role has. */
function pastDayWords(
  overall: { forecast: boolean; timeline: boolean; requirements: boolean; risks: boolean },
  label: string,
): string {
  const kept = [
    overall.forecast ? "the forecast" : overall.timeline ? "the pods' dates" : null,
    overall.requirements ? "requirements" : null,
    overall.risks ? "risks" : null,
  ].filter((part): part is string => part !== null);
  if (kept.length === 0) return "";
  const joined =
    kept.length === 1 ? kept[0] : `${kept.slice(0, -1).join(", ")} and ${kept[kept.length - 1]}`;
  const one = kept.length === 1 && kept[0] === "the forecast";
  return `${joined.charAt(0).toUpperCase()}${joined.slice(1)} ${one ? "is as it" : "are as they"} stood on ${label}.`;
}
