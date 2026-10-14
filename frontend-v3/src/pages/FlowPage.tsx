import type { Scope } from "../app/access";
import {
  podsOfPerson,
  projectsOfPerson,
  rankProjects,
  useMemberId,
  usePods,
  useProjects,
} from "../app/directory";
import { useRole } from "../app/role";
import { PanelState, SectionHeader } from "../components/PanelState";
import { PrFlowSection } from "../features/signals/PrFlowSection";

/**
 * Review flow (`/flow`): where work waits in review, from Git, whatever
 * anyone reports. What was Signals › Flow, a page of its own under Insight:
 * stage times, the worst jam, work by type and where the time goes. It opens on
 * the viewer's own part: a scrum master's first pod, a product owner's first
 * project, everything for the portfolio roles; "All repositories" is a pick
 * away, and the pick lives in the URL.
 */
export function FlowPage() {
  const { signals } = useRole().access;
  return (
    <>
      <SectionHeader
        title="Review flow"
        meta="How long work waits in review, from Git, whatever anyone reports."
      />
      <Flow scope={signals.flowScope} />
    </>
  );
}

function Flow({ scope }: { scope: Scope }) {
  const memberId = useMemberId();
  const pods = usePods();
  const projects = useProjects();
  const own = podsOfPerson(pods.data ?? [], memberId);
  const ownProjects = projectsOfPerson(projects.data ?? [], own.pods, own.own);
  const waiting =
    scope !== "all" && (pods.isPending || (scope === "projects" && projects.isPending));
  const firstPod = own.own ? own.pods[0] : undefined;
  const firstProject = ownProjects.own
    ? rankProjects(ownProjects.projects, own.pods)[0]
    : undefined;
  const opensOn =
    scope === "pods" && firstPod
      ? `pod:${firstPod.id}`
      : scope === "projects" && firstProject
        ? `project:${firstProject.id}`
        : "all";
  if (waiting) {
    return (
      <PanelState isLoading error={null}>
        {null}
      </PanelState>
    );
  }
  return <PrFlowSection full enabled defaultScope={opensOn} />;
}
