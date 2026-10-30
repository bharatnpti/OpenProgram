import { useRole } from "../../app/role";
import { Anchored, SectionList } from "../../features/hub/SectionList";
import { TabIntro } from "../../features/hub/TabIntro";
import { GateAnswer } from "../../features/overall/AnswerCard";
import { BaselineSection } from "../../features/overall/BaselineSection";
import { GatesSection } from "../../features/overall/GatesSection";
import { HygieneSection } from "../../features/overall/HygieneSection";
import { useReadsReadiness } from "../../features/overall/queries";
import { ReadinessSection } from "../../features/overall/ReadinessSection";
import { projectSections } from "../../features/project/sections";

/**
 * Project › Scope control (`/projects/:projectId/scope`): against the plan
 * (the planning baseline, Mark planning complete, New baseline), then fit to
 * move on (the acceptance gates with their dialog, and release readiness),
 * then Jira hygiene. Each as the reader's project board allows: the project's
 * readers act, an executive reads hygiene as counts and the gates; a scrum
 * master the hygiene, gates and readiness where their pods work; a developer
 * the gates, with where they stand for them first.
 */
export function ProjectScope({ projectId, releaseId }: { projectId: string; releaseId: string }) {
  const { access } = useRole();
  const readiness = useReadsReadiness(projectId);
  const developer = !access.overall.forecast && !access.overall.timeline;
  const drawn = projectSections("scope", {
    baseline: access.overall.forecast ? (
      <Anchored id="baseline">
        <BaselineSection projectId={projectId} releaseId={releaseId} />
      </Anchored>
    ) : null,
    gates: (
      <Anchored id="gates">
        <div className="grid grid-cols-[minmax(0,1fr)] gap-6">
          {developer ? <GateAnswer projectId={projectId} releaseId={releaseId} /> : null}
          <GatesSection projectId={projectId} releaseId={releaseId} summary={!developer} />
        </div>
      </Anchored>
    ),
    readiness: readiness ? (
      <ReadinessSection
        projectId={projectId}
        releaseId={releaseId}
        answered={access.overall.forecast}
      />
    ) : null,
    hygiene: readiness ? <HygieneSection projectId={projectId} releaseId={releaseId} /> : null,
  });
  return (
    <div>
      <TabIntro tab="scope" />
      <SectionList drawn={drawn} />
    </div>
  );
}
