import { useRole } from "../../app/role";
import { Anchored, SectionList } from "../../features/hub/SectionList";
import { TabIntro } from "../../features/hub/TabIntro";
import { QuestionsSection } from "../../features/overall/QuestionsSection";
import { RisksSection } from "../../features/overall/RisksSection";
import { projectSections } from "../../features/project/sections";

/**
 * Project › Early warnings (`/projects/:projectId/warnings`): every warning on
 * this project with who resolves it and what its owner says (folded), and the
 * questions asked of it. An executive reads no names (no who-resolves, no
 * quotes); a developer reads the questions only. An admin's list links to
 * Admin › Escalation, where who is told when is kept.
 */
export function ProjectWarnings({
  projectId,
  releaseId,
}: {
  projectId: string;
  releaseId: string;
}) {
  const { access } = useRole();
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
    </div>
  );
}
