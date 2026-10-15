import { useOwnTree, usePods, useProjects, useWorkstreams } from "../../app/directory";
import { useRole } from "../../app/role";
import { ownProjects } from "../../features/delivery/ownTree";
import { Anchored, SectionList } from "../../features/hub/SectionList";
import { TabIntro } from "../../features/hub/TabIntro";
import { ProjectAnswer, ScrumMasterAnswer } from "../../features/overall/AnswerCard";
import { RequirementsSection } from "../../features/overall/RequirementsSection";
import { TimelineSection } from "../../features/overall/TimelineSection";
import { ProjectWork } from "../../features/project/ProjectWork";
import { projectSections } from "../../features/project/sections";
import { WhatChanged } from "../../features/project/WhatChanged.tsx";
import { workPods } from "../../features/project/workPods";

/**
 * Project › Will we make the date? (`/projects/:projectId/date`): the answer
 * with the three dates; the timeline, What changed for the row picked on it
 * and why its date moved; requirements by stage; the work (progress, tasks,
 * pods, workstreams). Each section is its own read, so one refused or slow
 * read never blanks the others.
 *
 * Each role gets the sections it reads (app/access.ts): the project's readers
 * all of it, an executive no names; a scrum master their pod's answer and the
 * timeline's pods (theirs whole) and their pods' work. A developer has no
 * project dates, so no date tab.
 */
export function ProjectDate({ projectId, releaseId }: { projectId: string; releaseId: string }) {
  const { access, canReadProjectProgress } = useRole();
  const projects = useProjects();
  const pods = usePods();
  const workstreams = useWorkstreams();
  const tree = useOwnTree();
  const project = projects.data?.find((item) => item.id === projectId);
  const treeProject = tree.data
    ? ownProjects(tree.data).find((item) => item.id === projectId)
    : undefined;
  const forecast = access.overall.forecast;
  const scrumMaster = !forecast && access.overall.timeline;
  const readable = canReadProjectProgress || Boolean(treeProject?.full);
  const workPodsList = workPods(
    project,
    pods.data ?? [],
    access.deliveryScope === "own" ? (treeProject?.pods ?? null) : null,
    access.deliveryScope === "all" && !access.hub.countsOnly,
  );
  const projectWorkstreams = (workstreams.data ?? []).filter((item) =>
    project?.workstream_ids.includes(item.id),
  );

  const drawn = projectSections("date", {
    answer: forecast ? (
      <ProjectAnswer projectId={projectId} releaseId={releaseId} chip={false} />
    ) : scrumMaster ? (
      <ScrumMasterAnswer projectId={projectId} />
    ) : null,
    timeline: access.overall.timeline ? (
      <TimelineSection
        projectId={projectId}
        releaseId={releaseId}
        between={(picked) => <WhatChanged {...picked} />}
      />
    ) : null,
    requirements: access.overall.requirements ? (
      <Anchored id="stages">
        <RequirementsSection projectId={projectId} releaseId={releaseId} />
      </Anchored>
    ) : null,
    work: (
      <ProjectWork
        project={project}
        pods={workPodsList}
        workstreams={projectWorkstreams}
        readable={readable}
      />
    ),
  });

  return (
    <div>
      <TabIntro tab="date" />
      <SectionList drawn={drawn} />
    </div>
  );
}
