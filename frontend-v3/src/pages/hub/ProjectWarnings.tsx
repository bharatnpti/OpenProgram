import { useRole } from "../../app/role";
import { Anchored, SectionList } from "../../features/hub/SectionList";
import { TabIntro } from "../../features/hub/TabIntro";
import { useHubProject } from "../../features/hub/useHub";
import { QuestionsSection } from "../../features/overall/QuestionsSection";
import { RisksSection } from "../../features/overall/RisksSection";
import { projectSections } from "../../features/project/sections";
import { ProjectReviewFlow, TodaysHealth } from "../../features/project/TodaysHealth";

/**
 * Project › Early warnings (`/projects/:projectId/warnings`): every warning on
 * this project with who resolves it and what its owner says (folded), the
 * questions asked of it, its health today apart from the date (what sets its
 * colour, every reason, the last 30 days), and its review flow in the compact
 * form. An executive reads no names (no who-resolves, no quotes, no reasons'
 * people); a developer reads the questions only. An admin's list links to
 * Admin › Escalation, where who is told when is kept.
 */
export function ProjectWarnings({
  projectId,
  releaseId,
}: {
  projectId: string;
  releaseId: string;
}) {
  const { access, canReadAggregate } = useRole();
  const { name } = useHubProject(projectId);
  const drawn = projectSections("warnings", {
    warnings: access.overall.risks ? (
      <Anchored id="warnings">
        <RisksSection
          projectId={projectId}
          title="Warnings for this project"
          names={!access.hub.countsOnly}
          escalation={access.overall.escalationLink}
        />
      </Anchored>
    ) : null,
    questions: (
      <Anchored id="questions">
        <QuestionsSection projectId={projectId} releaseId={releaseId} />
      </Anchored>
    ),
  });
  return (
    <div>
      <TabIntro tab="warnings" />
      <SectionList drawn={drawn} />
      {/* Health and review flow are the aggregate readers': a developer reads the questions. */}
      {canReadAggregate ? (
        <div className="mt-10 grid grid-cols-[minmax(0,1fr)] gap-10 max-sm:mt-8 max-sm:gap-8">
          <TodaysHealth projectId={projectId} name={name ?? "This project"} />
          <ProjectReviewFlow projectId={projectId} />
        </div>
      ) : null}
    </div>
  );
}
