import { useQuery } from "@tanstack/react-query";
import { Fragment, useEffect, type ReactNode } from "react";
import { Link, Navigate, useLocation, useParams, useSearchParams } from "react-router-dom";

import { ApiError, apiClient } from "../api/client";
import { useOwnTree, usePods, usePrograms, useProjects, useWorkstreams } from "../app/directory";
import { projectPath } from "../app/links";
import { useRole } from "../app/role";
import { useDayWords, useViewingDate } from "../app/viewingDate";
import { PanelState } from "../components/PanelState";
import { useAssistantSubject } from "../features/assistant/assistantContext";
import { setBy } from "../features/delivery/factors";
import { ownProjects } from "../features/delivery/ownTree";
import { GateAnswer, ProjectAnswer, ScrumMasterAnswer } from "../features/overall/AnswerCard";
import { GatesSection } from "../features/overall/GatesSection";
import {
  useDelivery,
  useGateBoard,
  useReadsReadiness,
  useReleases,
} from "../features/overall/queries";
import { QuestionsSection } from "../features/overall/QuestionsSection";
import { ReadinessSection } from "../features/overall/ReadinessSection";
import { ReleaseScope } from "../features/overall/ReleaseScope";
import { RequirementsSection } from "../features/overall/RequirementsSection";
import { RisksSection } from "../features/overall/RisksSection";
import { TimelineSection } from "../features/overall/TimelineSection";
import { ProjectHeader } from "../features/project/ProjectHeader";
import { ProjectWork } from "../features/project/ProjectWork";
import { workPods } from "../features/project/workPods";
import { defaultProject } from "../features/project/projectChoice";
import { ScopeLine } from "../features/project/ScopeLine";
import { projectAnchors, projectSections } from "../features/project/sections";
import { useProjectOptions } from "../features/project/useProjectOptions";
import { WhatChanged } from "../features/project/WhatChanged.tsx";

/**
 * One project, at `/projects/:projectId` (D86): one page for every project, the
 * URL its address, never a nav entry per project. A bare `/projects` opens the
 * worst of the person's own (else the last opened, else the worst), keeping
 * the link's query and hash. The URL always wins.
 */
export function ProjectPage() {
  const { projectId = "" } = useParams();
  const location = useLocation();
  const day = useDayWords();
  const choice = useProjectOptions();

  if (!projectId) {
    const id = choice.loading ? null : defaultProject(choice.options, choice.recent);
    if (id) {
      return (
        <Navigate
          to={{ pathname: projectPath(id), search: location.search, hash: location.hash }}
          replace
        />
      );
    }
    return (
      <PanelState
        isLoading={choice.loading}
        error={choice.error}
        isEmpty
        emptyText={
          day === "today"
            ? "No project is yours to open yet."
            : `No project was yours to open ${day}.`
        }
      >
        {null}
      </PanelState>
    );
  }
  // A different project is a different page: nothing of the last one's state stays.
  return <ProjectView key={projectId} projectId={projectId} choice={choice} />;
}

/**
 * The page, top to bottom (perspective-ux §3.3, features/project/sections.ts):
 * the header with the switcher and the date verdict; the answer; the timeline,
 * What changed for the row picked on it and why its date moved; scope in one
 * line; requirements by stage; the gates and release readiness; questions;
 * this project's early warnings; the work. Each thing is said once: the answer
 * and the timeline say the dates, so nothing below repeats them. Each section is
 * its own read, so one refused or slow read never blanks the others.
 *
 * Each role gets the sections it reads (app/access.ts, perspective-roles §1):
 * - the project's readers (product owner, manager, executive, admin): all of
 *   it; an executive reads no names (no owner line, no "who resolves", no
 *   people, no reasons behind today's health) and Jira hygiene as counts;
 * - a scrum master: their pod's answer, the timeline's pods (theirs whole),
 *   hygiene, gates and readiness where their pods work, questions, warnings and
 *   their pods' work;
 * - a developer: the name-only form, the pods with their own one open, and the
 *   gates and questions they read on Overall.
 */
function ProjectView({
  projectId,
  choice,
}: {
  projectId: string;
  choice: ReturnType<typeof useProjectOptions>;
}) {
  const { access, canReadProjectProgress } = useRole();
  const [search, setSearch] = useSearchParams();
  const { asOf, label } = useViewingDate();
  const projects = useProjects();
  const programs = usePrograms();
  const pods = usePods();
  const workstreams = useWorkstreams();
  const tree = useOwnTree();
  const { opened } = choice;

  const project = projects.data?.find((item) => item.id === projectId);
  const treeProject = tree.data
    ? ownProjects(tree.data).find((item) => item.id === projectId)
    : undefined;
  const name = project?.name ?? treeProject?.name ?? "Project";
  const forecast = access.overall.forecast;
  const scrumMaster = !forecast && access.overall.timeline;
  const developer = !forecast && !access.overall.timeline;
  // An executive reads every project but no pod: the one role names are kept from (D87).
  const executive = access.pages.portfolio && !access.pages.pods;
  const readable = canReadProjectProgress || Boolean(treeProject?.full);

  useAssistantSubject(project || treeProject ? { kind: "project", name } : null);
  useEffect(() => {
    if (project || treeProject) opened(projectId);
  }, [opened, project, projectId, treeProject]);

  // The release in the link, unless it is gone (Overall's rule): a reader who
  // cannot list releases learns it from the gate board, the gates section's read.
  const asked = search.get("release") ?? "";
  const releases = useReleases(projectId);
  const probe = useGateBoard(projectId, !releases.readable && asked ? asked : undefined);
  const goneForReader =
    !releases.readable && probe.query.error instanceof ApiError && probe.query.error.status === 404;
  const list = releases.query.data;
  const gone =
    asked !== "" && ((list && !list.some((r) => r.release_id === asked)) || goneForReader);
  const releaseId = gone ? "" : asked;

  const delivery = useDelivery(projectId);
  const scope = releaseId
    ? delivery.query.data?.releases.find((item) => item.scope_id === releaseId)
    : delivery.query.data?.project;
  // Today's health, said in words (D92): the same read as the Work section's progress.
  const progress = useQuery({
    queryKey: ["project", projectId, "progress"],
    queryFn: () => apiClient.projectProgress(projectId),
    enabled: readable && !executive,
  });
  // The reason itself; where it came from is under Work's "every reason".
  const health = progress.data
    ? (setBy(progress.data.rag, progress.data.factors)?.text ?? null)
    : null;

  const readiness = useReadsReadiness(projectId);
  const programName =
    programs.data?.find((program) => program.id === project?.program_ids[0])?.name ?? null;
  const ownPods = treeProject ? treeProject.pods : null;
  const workPodsList = workPods(
    project,
    pods.data ?? [],
    access.deliveryScope === "own" ? ownPods : null,
    access.deliveryScope === "all" && access.pages.pods,
  );
  const projectWorkstreams = (workstreams.data ?? []).filter((item) =>
    project?.workstream_ids.includes(item.id),
  );

  const anchor = (id: string, element: ReactNode) => (
    <div id={id} className="scroll-mt-28">
      {element}
    </div>
  );
  const drawn = projectSections({
    answer: forecast ? (
      <ProjectAnswer projectId={projectId} releaseId={releaseId} chip={false} />
    ) : scrumMaster ? (
      <ScrumMasterAnswer projectId={projectId} />
    ) : (
      <GateAnswer projectId={projectId} releaseId={releaseId} />
    ),
    timeline: access.overall.timeline ? (
      <TimelineSection
        projectId={projectId}
        releaseId={releaseId}
        between={(picked) => <WhatChanged {...picked} />}
      />
    ) : null,
    scope: developer ? null : <ScopeLine projectId={projectId} releaseId={releaseId} />,
    requirements: access.overall.requirements
      ? anchor("stages", <RequirementsSection projectId={projectId} releaseId={releaseId} />)
      : null,
    gates: anchor(
      "gates",
      <GatesSection projectId={projectId} releaseId={releaseId} summary={!developer} />,
    ),
    readiness: readiness ? (
      <ReadinessSection projectId={projectId} releaseId={releaseId} answered={forecast} />
    ) : null,
    questions: anchor(
      "questions",
      <QuestionsSection projectId={projectId} releaseId={releaseId} />,
    ),
    warnings: access.overall.risks
      ? anchor(
          "warnings",
          <RisksSection
            projectId={projectId}
            title="Early warnings on this project"
            limit={access.pages.warnings ? 5 : undefined}
            names={!executive}
            escalation={false}
            footer={
              access.pages.warnings ? (
                <Link
                  to={`/warnings?project=${encodeURIComponent(projectId)}`}
                  className="w-fit text-[13px] font-bold max-sm:inline-flex max-sm:min-h-11 max-sm:items-center"
                >
                  All early warnings →
                </Link>
              ) : null
            }
          />,
        )
      : null,
    work: (
      <ProjectWork
        project={project}
        pods={workPodsList}
        workstreams={projectWorkstreams}
        readable={readable}
        namesOnly={developer}
      />
    ),
  });
  const anchors = projectAnchors(drawn.map(({ key }) => key));
  useHashLanding(drawn.length > 0);

  return (
    <div className="op-viz">
      <ProjectHeader
        projectId={projectId}
        name={name}
        project={project}
        programName={programName}
        scope={forecast ? scope : undefined}
        health={executive ? null : health}
        names={!executive}
        choice={choice}
      />
      <ReleaseScope
        projectId={projectId}
        releaseId={releaseId}
        onChange={(next) =>
          setSearch(
            (current) => {
              // The row picked belongs to the old scope; the viewing day is the shell's.
              const params = new URLSearchParams(current);
              params.delete("row");
              if (next) params.set("release", next);
              else params.delete("release");
              return params;
            },
            { replace: true },
          )
        }
      />
      {asked && !releaseId ? (
        <p className={`${releases.readable ? "-mt-5" : ""} mb-8 text-[13px] text-grey-secondary`}>
          The release in the link no longer exists, so this shows the whole project.
        </p>
      ) : null}
      {asOf ? (
        <p className="mb-8 rounded-2xl bg-grey-fill px-4 py-3 text-[13px] text-grey-body">
          {pastDayWords(access.overall, label ?? "")} Gate sign-offs and questions are not kept per
          day: they show how they stand now, for the requirements of that day.
          {readiness ? " Release readiness shows how it stands now." : ""}
        </p>
      ) : null}
      {anchors.length > 0 ? (
        <nav
          aria-label="On this page"
          className="sticky z-20 -mx-1 mb-8 flex gap-1 overflow-x-auto bg-(--op-viz-surface) px-1 py-2 [scrollbar-width:none] max-md:[mask-image:linear-gradient(to_right,black_calc(100%-28px),transparent)]"
          style={{ top: "var(--op-header-h, 64px)" }}
        >
          {anchors.map((item) => (
            <a
              key={item.id}
              href={`#${item.id}`}
              className="flex-none rounded-full border border-grey-border px-3 py-1.5 text-[13px] font-bold text-grey-body no-underline hover:bg-grey-fill hover:text-ink"
            >
              {item.label}
            </a>
          ))}
        </nav>
      ) : null}
      <div className="grid grid-cols-[minmax(0,1fr)] gap-10 max-sm:gap-8">
        {drawn.map(({ key, element }) => (
          <Fragment key={key}>{element}</Fragment>
        ))}
      </div>
    </div>
  );
}

/**
 * A link to a section (`#gates`, `#warnings`…) lands on it once it is drawn:
 * the sections read their data first. The timeline lands by itself.
 */
function useHashLanding(ready: boolean) {
  const { hash } = useLocation();
  useEffect(() => {
    const id = decodeURIComponent(hash.slice(1));
    if (!ready || !id || id === "timeline") return;
    let tries = 0;
    const timer = window.setInterval(() => {
      const element = document.getElementById(id);
      tries += 1;
      if (element || tries > 25) {
        window.clearInterval(timer);
        element?.scrollIntoView({ block: "start" });
      }
    }, 200);
    return () => window.clearInterval(timer);
  }, [hash, ready]);
}

/** What a past day shows as it stood, named by the sections this role has. */
function pastDayWords(
  overall: { forecast: boolean; timeline: boolean; requirements: boolean; risks: boolean },
  label: string,
): string {
  const kept = [
    overall.forecast ? "the forecast" : overall.timeline ? "the pods' dates" : null,
    overall.requirements ? "requirements" : null,
    overall.risks ? "warnings" : null,
  ].filter((part): part is string => part !== null);
  if (kept.length === 0) return "";
  const joined =
    kept.length === 1 ? kept[0] : `${kept.slice(0, -1).join(", ")} and ${kept[kept.length - 1]}`;
  const one = kept.length === 1 && kept[0] === "the forecast";
  return `${joined.charAt(0).toUpperCase()}${joined.slice(1)} ${one ? "is as it" : "are as they"} stood on ${label}.`;
}
