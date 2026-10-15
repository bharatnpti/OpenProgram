import { useSearchParams } from "react-router-dom";

import { useOwnTree } from "../../app/directory";
import { useRole } from "../../app/role";
import { ownProjects } from "../../features/delivery/ownTree";
import { Anchored, SectionList } from "../../features/hub/SectionList";
import { TabIntro } from "../../features/hub/TabIntro";
import { ProjectAnswer, ScrumMasterAnswer } from "../../features/overall/AnswerCard";
import { RequirementsSection } from "../../features/overall/RequirementsSection";
import { TimelineSection } from "../../features/overall/TimelineSection";
import { NeedsAttention } from "../../features/project/NeedsAttention";
import { projectSections } from "../../features/project/sections";
import { WhatChanged } from "../../features/project/WhatChanged.tsx";
import { Workstreams } from "../../features/project/Workstreams";

/**
 * Project › Will we make the date? (`/projects/:projectId/date`): the answer
 * with the three dates; the timeline, What changed for the row picked on it
 * and why its date moved; requirements by stage; then the work: progress and
 * the tasks that need a decision (every task a click away), and the
 * workstreams where the project uses any (`?workstream=` opens one). The pods
 * are the pod bar above; the reasons behind today's health are on Early
 * warnings, said once there. Each section is its own read, so one refused or
 * slow read never blanks the others.
 *
 * Each role gets the sections it reads (app/access.ts): the project's readers
 * all of it, an executive no names; a scrum master their pod's answer and the
 * timeline's pods (theirs whole), and the work of a project their pods work
 * on. A developer has no project dates, so no date tab.
 */
export function ProjectDate({ projectId, releaseId }: { projectId: string; releaseId: string }) {
  const { access, canReadProjectProgress } = useRole();
  const [search] = useSearchParams();
  const tree = useOwnTree();
  const treeProject = tree.data
    ? ownProjects(tree.data).find((item) => item.id === projectId)
    : undefined;
  const forecast = access.overall.forecast;
  const scrumMaster = !forecast && access.overall.timeline;
  const readable = canReadProjectProgress || Boolean(treeProject?.full);
  // A link to one workstream lands on it: the workstreams come first then.
  const workstreamFirst = search.get("workstream") !== null;
  const work = readable ? (
    <Anchored id="work">
      <div className="grid grid-cols-[minmax(0,1fr)] gap-10 max-sm:gap-8">
        {workstreamFirst ? <Workstreams projectId={projectId} /> : null}
        <NeedsAttention projectId={projectId} />
        {workstreamFirst ? null : <Workstreams projectId={projectId} />}
      </div>
    </Anchored>
  ) : null;

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
    work,
  });

  return (
    <div>
      <TabIntro tab="date" />
      <SectionList drawn={drawn} />
    </div>
  );
}
